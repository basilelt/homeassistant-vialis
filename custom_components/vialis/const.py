DOMAIN = "vialis"
BASE_URL = "https://aelgrd.vialis.net/application"
# Public PKCE OAuth client ID embedded in the Vialis web portal (not a user secret)
CLIENT_ID = "xoq0D6pHtgG8NPXmdP5UVHSbmE26BS"
REDIRECT_URI = "https://aelgrd.vialis.net/autorisation-callback.html"
CODE_VERIFIER = "3"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
# Poll interval — configurable via the integration's options flow
CONF_UPDATE_HOURS = "update_hours"
DEFAULT_UPDATE_HOURS = 6   # 4×/day; data is daily-granularity, polling faster is wasteful
MIN_UPDATE_HOURS = 1
MAX_UPDATE_HOURS = 24
# Fixed anchor date for full-history import (before any French Linky rollout).
# Using a fixed date (not rolling days_back) ensures cumulative sums never drift
# across HA restarts — once imported, old entries are always re-imported with
# the same baseline.
HISTORY_START = "2018-01-01T00:00:00.000+02:00"
HISTORY_DAYS_INCREMENTAL = 5
# EDF/Vialis tariff rate in EUR/kWh — update when the rate changes
ENERGY_PRICE_EUR_PER_KWH = 0.1934
