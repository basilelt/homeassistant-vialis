import logging
import hashlib
import base64
import zoneinfo
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN, BASE_URL, CLIENT_ID, REDIRECT_URI, CODE_VERIFIER,
    CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS, CONF_USERNAME, CONF_PASSWORD,
    HISTORY_START, HISTORY_DAYS_INCREMENTAL, ENERGY_PRICE_EUR_PER_KWH,
)
from ._stats import anchored

_LOGGER = logging.getLogger(__name__)
_TZ = zoneinfo.ZoneInfo("Europe/Paris")

_GROUPES = [
    {"typeObjet": "produit.GroupeGrandeur", "codeGroupeGrandeur": {"code": "0"}},  # courbe de charge
    {"typeObjet": "produit.GroupeGrandeur", "codeGroupeGrandeur": {"code": "2"}},  # consommations HP
    {"typeObjet": "produit.GroupeGrandeur", "codeGroupeGrandeur": {"code": "3"}},  # consommations HC
    {"typeObjet": "produit.GroupeGrandeur", "codeGroupeGrandeur": {"code": "4"}},  # puissance maximale
]


class VialisCoordinator(DataUpdateCoordinator):

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        hours = int(entry.options.get(CONF_UPDATE_HOURS, DEFAULT_UPDATE_HOURS))
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=timedelta(hours=hours))
        self._username = entry.data[CONF_USERNAME]
        self._password = entry.data[CONF_PASSWORD]
        self.token: str | None = None
        self.token_expiry: datetime | None = None
        self.pasc_id: str | None = None
        self._history_loaded = False
        # Accumulated stores (survive incremental updates, reset on HA restart)
        # {mnemo: {naive_date: kwh}}
        self._all_daily: dict[str, dict[datetime, float]] = {}
        self._tariff_meta: dict[str, str] = {}
        # {(naive_date, "HH:MM"): watts}
        self._all_courbe: dict[tuple[datetime, str], float] = {}

    # ------------------------------------------------------------------ auth

    def _is_token_valid(self) -> bool:
        return bool(self.token and self.token_expiry and datetime.now() < self.token_expiry)

    async def _authenticate(self, session: aiohttp.ClientSession) -> None:
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(CODE_VERIFIER.encode()).digest())
            .decode().rstrip("=")
        )
        resp1 = await session.post(
            f"{BASE_URL}/auth/externe/authentification",
            data={"username": self._username, "password": self._password, "client_id": CLIENT_ID},
        )
        resp1.raise_for_status()
        body1 = await resp1.json(content_type=None)
        if body1.get("code") != "0":
            raise UpdateFailed(f"Vialis auth step 1 failed: {body1}")

        resp2 = await session.get(
            f"{BASE_URL}/auth/authorize-internet",
            params={"redirect_uri": REDIRECT_URI, "response_type": "code",
                    "code_challenge": code_challenge, "code_challenge_method": "S256",
                    "client_id": CLIENT_ID},
            allow_redirects=False,
        )
        location = resp2.headers.get("Location", "")
        codes = parse_qs(urlparse(location).query).get("code")
        if not codes:
            raise UpdateFailed(f"Vialis auth step 2: no code in Location={location!r}")
        auth_code = codes[0]

        resp3 = await session.post(
            f"{BASE_URL}/auth/tokenUtilisateurInternet",
            data={"client_id": CLIENT_ID, "code": auth_code, "redirect_uri": REDIRECT_URI,
                  "grant_type": "authorization_code", "code_verifier": CODE_VERIFIER},
        )
        resp3.raise_for_status()
        token_body = await resp3.json(content_type=None)
        access_token = token_body.get("access_token")
        if not access_token:
            raise UpdateFailed(f"Vialis auth step 3: no access_token: {token_body}")
        self.token = access_token
        self.token_expiry = datetime.now() + timedelta(seconds=int(token_body.get("expires_in", 3600)) - 60)
        _LOGGER.debug("Vialis auth OK, expires %s", self.token_expiry)

    async def _ensure_authenticated(self, session: aiohttp.ClientSession) -> None:
        if not self._is_token_valid():
            await self._authenticate(session)

    # ------------------------------------------------------------------ API

    async def _fetch_pasc_id(self, session: aiohttp.ClientSession) -> str:
        resp = await session.get(
            f"{BASE_URL}/rest/produits/contrats",
            headers={"Authorization": f"Bearer {self.token}"},
        )
        resp.raise_for_status()
        contracts = await resp.json(content_type=None)
        for c in contracts:
            pid = (c.get("pointAccesServicesClient") or {}).get("id")
            if pid:
                return str(pid)
        raise UpdateFailed("Vialis: no PASC id found in contracts")

    async def _fetch_historique(self, session: aiohttp.ClientSession, date_from: str) -> dict:
        now = datetime.now()
        body = {
            "typeObjet": "DonneesHistoriqueMesureRepresentation",
            "dateDebut": date_from,
            "dateFin": now.strftime("%Y-%m-%dT23:59:59.000+02:00"),
            "pointAccesServicesClient": {"typeObjet": "produit.PointAccesServicesClient", "id": self.pasc_id},
            "groupesDeGrandeurs": _GROUPES,
        }
        resp = await session.post(
            f"{BASE_URL}/rest/interfaces/aelgrd/historiqueDeMesure",
            json=body,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return await resp.json(content_type=None)

    # ------------------------------------------------------------------ parsing

    @staticmethod
    def _parse_raw(data: dict) -> tuple[
        dict[str, dict[datetime, float]],
        dict[str, str],
        list[dict],
        list[dict],
    ]:
        """
        Returns (tariff_daily, tariff_meta, pmax_entries, courbe_entries).
        tariff_daily: {mnemo: {naive_date: kwh}}
        tariff_meta:  {mnemo: libelle}
        """
        tariff_daily: dict[str, dict[datetime, float]] = {}
        tariff_meta: dict[str, str] = {}
        pmax_entries: list[dict] = []
        courbe_entries: list[dict] = []

        for periode in data.get("periodesActivite", []):
            # --- consommations (codes 2 & 3) ---
            bloc = periode.get("blocGRD") or {}
            for poste in bloc.get("postesHorosaisonnier") or []:
                mnemo = poste.get("mnemo", "UNKNOWN")
                tariff_meta[mnemo] = poste.get("libelle", mnemo)
                if mnemo not in tariff_daily:
                    tariff_daily[mnemo] = {}
                for entry in poste.get("consommationsJournalieres") or []:
                    val = entry.get("consommation")
                    if val is None:
                        continue
                    date_str = entry.get("date", "")
                    try:
                        d = datetime.strptime(date_str, "%d/%m/%Y")
                    except (ValueError, TypeError):
                        continue
                    tariff_daily[mnemo][d] = float(val)

            # --- puissance maximale (code 4) ---
            pm = periode.get("puissancesMaximales") or {}
            for entry in pm.get("puissancesJournalieres") or []:
                date_str = entry.get("date", "")
                try:
                    d = datetime.strptime(date_str, "%d/%m/%Y")
                except (ValueError, TypeError):
                    continue
                pmax_entries.append({
                    "date": date_str,
                    "heure": entry.get("heure", ""),
                    "kva": float(entry.get("puissanceMaximale") or 0),
                    "dt": d,
                })

            # --- courbe de charge (code 0) ---
            courbe = periode.get("courbe") or {}
            for entry in courbe.get("valeurs") or []:
                val = entry.get("valeur")
                if val is None:
                    continue
                date_str = entry.get("date", "")
                try:
                    d = datetime.strptime(date_str, "%d/%m/%Y")
                except (ValueError, TypeError):
                    continue
                courbe_entries.append({
                    "date": date_str,
                    "heure": entry.get("heure", ""),
                    "watts": float(val),
                    "dt": d,
                })

        return tariff_daily, tariff_meta, pmax_entries, courbe_entries

    def _merge(
        self,
        tariff_daily: dict[str, dict[datetime, float]],
        tariff_meta: dict[str, str],
        courbe_entries: list[dict],
    ) -> None:
        for mnemo, daily in tariff_daily.items():
            if mnemo not in self._all_daily:
                self._all_daily[mnemo] = {}
            self._all_daily[mnemo].update(daily)
        self._tariff_meta.update(tariff_meta)
        for entry in courbe_entries:
            self._all_courbe[(entry["dt"], entry["heure"])] = entry["watts"]

    def _compute_current(self, pmax_entries: list, courbe_entries: list) -> dict:
        now = datetime.now()
        current_month, current_year = now.month, now.year

        all_dates = [d for daily in self._all_daily.values() for d in daily]
        latest_dt = max(all_dates) if all_dates else None
        latest_date_str = latest_dt.strftime("%d/%m/%Y") if latest_dt else None

        tariffs: dict[str, dict] = {}
        for mnemo, daily in self._all_daily.items():
            tariffs[mnemo] = {
                "latest_kwh": daily.get(latest_dt, 0.0) if latest_dt else 0.0,
                "monthly_kwh": sum(v for d, v in daily.items()
                                   if d.month == current_month and d.year == current_year),
                "libelle": self._tariff_meta.get(mnemo, mnemo),
            }

        pmax_latest = max(pmax_entries, key=lambda x: x["dt"]) if pmax_entries else None
        courbe_latest = max(courbe_entries, key=lambda x: (x["dt"], x["heure"])) if courbe_entries else None

        return {
            "latest_date": latest_date_str,
            "latest_kwh": sum(t["latest_kwh"] for t in tariffs.values()),
            "monthly_kwh": sum(t["monthly_kwh"] for t in tariffs.values()),
            "tariffs": tariffs,
            "pmax_kva": pmax_latest["kva"] if pmax_latest else None,
            "pmax_date": pmax_latest["date"] if pmax_latest else None,
            "pmax_heure": pmax_latest["heure"] if pmax_latest else None,
            "current_power_w": courbe_latest["watts"] if courbe_latest else None,
            "current_power_date": courbe_latest["date"] if courbe_latest else None,
            "current_power_heure": courbe_latest["heure"] if courbe_latest else None,
        }

    # ------------------------------------------------------------------ statistics

    @staticmethod
    def _epoch_to_dt(ts) -> datetime | None:
        if ts is None:
            return None
        ts = float(ts)
        if ts > 1e12:  # some recorder versions hand back milliseconds
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=_TZ)

    async def _fetch_last_sums(self, stat_ids: list[str]) -> dict | None:
        """Return {stat_id: (last_start_dt, last_sum)} from the recorder.

        Returns None on failure so the caller can skip the import rather than
        re-importing from a zero baseline and recreating a negative seam.
        """
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import get_last_statistics
        except Exception:
            return None

        def _query() -> dict:
            out: dict[str, tuple] = {}
            for sid in stat_ids:
                rows = (get_last_statistics(self.hass, 1, sid, True, {"sum"}) or {}).get(sid)
                if rows:
                    out[sid] = (self._epoch_to_dt(rows[0].get("start")), rows[0].get("sum") or 0.0)
            return out

        try:
            return await get_instance(self.hass).async_add_executor_job(_query)
        except Exception as e:
            _LOGGER.warning("Vialis: could not read recorder baselines: %s", e)
            return None

    def _import_statistics(self, baselines: dict, since: datetime | None = None) -> None:
        """
        Import energy + power statistics into the HA recorder.
        baselines: {stat_id: (last_start_dt, last_sum)} — cumulative series are
                   anchored to these so sums only ever grow (no negative bars).
        since: limits which load-curve mean buckets are recomputed (perf only).
        """
        try:
            from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
            from homeassistant.components.recorder.models.statistics import StatisticMeanType
            from homeassistant.components.recorder.statistics import async_add_external_statistics
        except Exception:
            _LOGGER.debug("Recorder not available, skipping statistics import")
            return

        self._import_energy_statistics(StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines)
        self._import_courbe_energy_statistics(StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines)
        self._import_courbe_cost_statistics(StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines)
        self._import_courbe_statistics(StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, since)

    def _import_energy_statistics(self, StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines):
        total_by_date: dict[datetime, float] = {}

        for mnemo, daily in self._all_daily.items():
            if not daily:
                continue
            libelle = self._tariff_meta.get(mnemo, mnemo)
            stat_id = f"{DOMAIN}:energy_{mnemo.lower()}"
            last_dt, base_sum = baselines.get(stat_id, (None, 0.0))
            last_key = last_dt.date() if last_dt else None
            items = []
            for dt, kwh in daily.items():
                total_by_date[dt] = total_by_date.get(dt, 0.0) + kwh
                items.append((dt.date(), kwh, dt))
            stats = []
            for dt, kwh, cumulative in anchored(items, last_key, base_sum or 0.0):
                start = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_TZ)
                stats.append(StatisticData(start=start, state=kwh, sum=cumulative))
            if not stats:
                continue
            try:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        mean_type=StatisticMeanType.NONE, has_sum=True,
                        name=f"Vialis {libelle}",
                        source=DOMAIN,
                        statistic_id=stat_id,
                        unit_class="energy",
                        unit_of_measurement="kWh",
                    ),
                    stats,
                )
                _LOGGER.debug("Imported %d energy stats for %s (id=%s)", len(stats), mnemo, stat_id)
            except Exception as e:
                _LOGGER.warning("Vialis stats import failed for %s: %s", stat_id, e)

        # Total across all tariffs
        if not total_by_date:
            return
        stat_id = f"{DOMAIN}:energy_total"
        last_dt, base_sum = baselines.get(stat_id, (None, 0.0))
        last_key = last_dt.date() if last_dt else None
        items = [(dt.date(), v, dt) for dt, v in total_by_date.items()]
        stats = []
        for dt, kwh, cumulative in anchored(items, last_key, base_sum or 0.0):
            start = dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=_TZ)
            stats.append(StatisticData(start=start, state=kwh, sum=cumulative))
        if stats:
            try:
                async_add_external_statistics(
                    self.hass,
                    StatisticMetaData(
                        mean_type=StatisticMeanType.NONE, has_sum=True,
                        name="Vialis Consommation Totale",
                        source=DOMAIN,
                        statistic_id=stat_id,
                        unit_class="energy",
                        unit_of_measurement="kWh",
                    ),
                    stats,
                )
                _LOGGER.debug("Imported %d total energy stats", len(stats))
            except Exception as e:
                _LOGGER.warning("Vialis stats import failed for %s: %s", stat_id, e)

    def _import_courbe_energy_statistics(self, StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines):
        """Import hourly energy (kWh) from courbe de charge for the energy dashboard.

        HA external statistics require top-of-hour timestamps, so we sum both 30-min
        readings within each hour: kWh = (W1 + W2) × 0.5 / 1000.
        """
        if not self._all_courbe:
            return

        hourly_kwh: dict[datetime, float] = {}
        for (dt, heure), watts in self._all_courbe.items():
            try:
                hour, minute = map(int, heure.split(":"))
                bucket = datetime(dt.year, dt.month, dt.day, hour, 0, 0, tzinfo=_TZ)
            except (ValueError, AttributeError):
                continue
            hourly_kwh[bucket] = hourly_kwh.get(bucket, 0.0) + watts * 0.5 / 1000

        stat_id = f"{DOMAIN}:energy_courbe"
        last_dt, base_sum = baselines.get(stat_id, (None, 0.0))
        items = [(b, v, b) for b, v in hourly_kwh.items()]
        stats = [
            StatisticData(start=b, state=kwh, sum=cumulative)
            for b, kwh, cumulative in anchored(items, last_dt, base_sum or 0.0)
        ]

        if not stats:
            return

        try:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE, has_sum=True,
                    name="Vialis Courbe de Charge kWh",
                    source=DOMAIN,
                    statistic_id=stat_id,
                    unit_class="energy",
                    unit_of_measurement="kWh",
                ),
                stats,
            )
            _LOGGER.debug("Imported %d courbe energy (kWh) hourly stats", len(stats))
        except Exception as e:
            _LOGGER.warning("Vialis stats import failed for %s: %s", stat_id, e)

    def _import_courbe_cost_statistics(self, StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, baselines):
        """Import cumulative energy cost (EUR) derived from courbe de charge.

        The HA Energy dashboard only shows cost for external statistics when
        stat_cost points to a separate cumulative EUR statistic — number_energy_price
        is silently ignored for external stats in HA 2026.x.  This method produces
        vialis:energy_courbe_cost mirroring vialis:energy_courbe multiplied by
        ENERGY_PRICE_EUR_PER_KWH so the dashboard can display cost.
        """
        if not self._all_courbe:
            return

        # Re-use the same hourly bucketing as _import_courbe_energy_statistics
        hourly_kwh: dict[datetime, float] = {}
        for (dt, heure), watts in self._all_courbe.items():
            try:
                hour, minute = map(int, heure.split(":"))
                bucket = datetime(dt.year, dt.month, dt.day, hour, 0, 0, tzinfo=_TZ)
            except (ValueError, AttributeError):
                continue
            hourly_kwh[bucket] = hourly_kwh.get(bucket, 0.0) + watts * 0.5 / 1000

        stat_id = f"{DOMAIN}:energy_courbe_cost"
        last_dt, base_sum = baselines.get(stat_id, (None, 0.0))
        items = [(b, v * ENERGY_PRICE_EUR_PER_KWH, b) for b, v in hourly_kwh.items()]
        stats = [
            StatisticData(start=b, state=eur, sum=cumulative_eur)
            for b, eur, cumulative_eur in anchored(items, last_dt, base_sum or 0.0)
        ]

        if not stats:
            return

        try:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.NONE, has_sum=True,
                    name="Vialis Courbe de Charge Cost",
                    source=DOMAIN,
                    statistic_id=stat_id,
                    unit_class=None,
                    unit_of_measurement="EUR",
                ),
                stats,
            )
            _LOGGER.debug("Imported %d courbe cost (EUR) hourly stats", len(stats))
        except Exception as e:
            _LOGGER.warning("Vialis stats import failed for %s: %s", stat_id, e)

    def _import_courbe_statistics(self, StatisticData, StatisticMetaData, StatisticMeanType, async_add_external_statistics, since):
        if not self._all_courbe:
            return

        # Aggregate 30-min readings to hourly mean power (W)
        hourly: dict[datetime, list[float]] = {}
        for (dt, heure), watts in self._all_courbe.items():
            if since is not None and dt < since:
                continue
            try:
                hour, minute = map(int, heure.split(":"))
                bucket = datetime(dt.year, dt.month, dt.day, hour, 0, 0)
            except (ValueError, AttributeError):
                continue
            hourly.setdefault(bucket, []).append(watts)

        if not hourly:
            return

        stats = []
        for bucket in sorted(hourly):
            mean_w = sum(hourly[bucket]) / len(hourly[bucket])
            start = bucket.replace(tzinfo=_TZ)
            stats.append(StatisticData(start=start, mean=mean_w))

        stat_id = f"{DOMAIN}:power_courbe"
        try:
            async_add_external_statistics(
                self.hass,
                StatisticMetaData(
                    mean_type=StatisticMeanType.ARITHMETIC, has_sum=False,
                    name="Vialis Courbe de Charge",
                    source=DOMAIN,
                    statistic_id=stat_id,
                    unit_class="power",
                    unit_of_measurement="W",
                ),
                stats,
            )
            _LOGGER.debug("Imported %d courbe hourly stats", len(stats))
        except Exception as e:
            _LOGGER.warning("Vialis stats import failed for %s: %s", stat_id, e)

    # ------------------------------------------------------------------ main update

    async def _do_update(self, session: aiohttp.ClientSession) -> dict:
        if self.pasc_id is None:
            self.pasc_id = await self._fetch_pasc_id(session)

        if not self._history_loaded:
            date_from = HISTORY_START
            since = None
        else:
            since_dt = (
                datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                - timedelta(days=HISTORY_DAYS_INCREMENTAL)
            )
            date_from = since_dt.strftime("%Y-%m-%dT00:00:00.000+02:00")
            since = since_dt

        data = await self._fetch_historique(session, date_from)
        tariff_daily, tariff_meta, pmax_entries, courbe_entries = self._parse_raw(data)
        self._merge(tariff_daily, tariff_meta, courbe_entries)

        stat_ids = [f"{DOMAIN}:energy_{m.lower()}" for m in self._all_daily]
        stat_ids += [f"{DOMAIN}:energy_total", f"{DOMAIN}:energy_courbe", f"{DOMAIN}:energy_courbe_cost"]
        baselines = await self._fetch_last_sums(stat_ids)
        if baselines is None:
            _LOGGER.warning("Vialis: skipping stats import this cycle (no recorder baseline)")
        else:
            self._import_statistics(baselines, since=since)

        if not self._history_loaded:
            self._history_loaded = True
            total_days = sum(len(d) for d in self._all_daily.values())
            _LOGGER.info(
                "Vialis: full history loaded (from %s), %d daily entries, %d courbe entries",
                HISTORY_START, total_days, len(self._all_courbe),
            )

        return self._compute_current(pmax_entries, courbe_entries)

    async def _async_update_data(self) -> dict:
        # One session for the full update cycle (auth + pasc_id + historique).
        # The cookie jar must be unsafe so the PKCE OAuth cookies survive across steps.
        async with aiohttp.ClientSession(
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        ) as session:
            try:
                await self._ensure_authenticated(session)
                return await self._do_update(session)
            except UpdateFailed:
                raise
            except aiohttp.ClientResponseError as err:
                if err.status == 401:
                    self.token = None
                    try:
                        await self._authenticate(session)
                        return await self._do_update(session)
                    except Exception as retry_err:
                        raise UpdateFailed(f"Vialis re-auth failed: {retry_err}") from retry_err
                raise UpdateFailed(f"Vialis HTTP {err.status}: {err}") from err
            except Exception as err:
                raise UpdateFailed(f"Vialis error: {err}") from err
