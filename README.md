# Vialis Linky — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Home Assistant custom integration for **Vialis** electricity customers (Martinique, Guadeloupe, Guyane). Pulls Linky smart-meter data directly from the Vialis customer portal — no physical USB dongle required.

> **Unofficial** — not affiliated with or endorsed by Vialis / EDF.

---

## Features

- **Sensors** — daily consumption (kWh), monthly consumption (kWh), max power demand (kVA), current load-curve reading (W), per-tariff sensors (HP/HC/…)
- **Long-term statistics** — imports full Linky history into the HA Energy Dashboard:
  - Per-tariff energy (`vialis:energy_<mnemo>`)
  - Total energy (`vialis:energy_total`)
  - Hourly load-curve energy (`vialis:energy_courbe`)
  - Hourly load-curve cost in EUR (`vialis:energy_courbe_cost`)
  - Hourly mean power (`vialis:power_courbe`)
- **Full history on first load** — fetches from 2018-01-01; incremental 5-day window on subsequent polls
- **Auto re-auth** — transparently re-authenticates on 401

---

## Requirements

- Home Assistant 2024.1 or newer
- A [Vialis customer portal](https://aelgrd.vialis.net) account (email + password)

---

## Installation via HACS

1. In HACS → **Integrations** → three-dot menu → **Custom repositories**
2. Add `https://github.com/basilelt/homeassistant-vialis` as type **Integration**
3. Install **Vialis Linky** and restart Home Assistant

### Manual installation

Copy `custom_components/vialis/` into your HA config directory:

```bash
cp -r custom_components/vialis /config/custom_components/
```

Restart Home Assistant.

---

## Configuration

1. **Settings → Devices & Services → Add Integration → Vialis Linky**
2. Enter your Vialis portal email and password

Credentials are stored in the HA config entry — never in plain text files.

---

## Energy Dashboard setup

After the first data load (may take a minute):

1. **Settings → Dashboards → Energy**
2. **Grid consumption → Add consumption** → select `Vialis Courbe de Charge kWh`
3. Optionally link the cost statistic `Vialis Courbe de Charge Cost` (EUR)

---

## Configuration constants

| Constant | File | Default | Notes |
|---|---|---|---|
| `ENERGY_PRICE_EUR_PER_KWH` | `const.py` | `0.1934` | Update to your actual tariff rate |
| `UPDATE_INTERVAL` | `const.py` | 30 min | Portal polling interval |
| `HISTORY_DAYS_INCREMENTAL` | `const.py` | 5 days | Rolling window for incremental updates |

---

## Troubleshooting

- **`invalid_auth`** — verify credentials at [aelgrd.vialis.net](https://aelgrd.vialis.net)
- **Statistics not appearing** — HA Recorder must be enabled; wait one full poll cycle
- **Wrong cost figures** — update `ENERGY_PRICE_EUR_PER_KWH` in `const.py` to match your contract rate

---

## Contributing

Issues and PRs welcome. This integration was built against the Vialis portal API as observed in 2025–2026; the API is undocumented and may change.

## License

[MIT](LICENSE)
