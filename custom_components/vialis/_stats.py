"""Pure helpers for Vialis statistics import (no Home Assistant imports).

Kept dependency-free so the cumulative-anchoring logic stays unit-testable
without a running Home Assistant.
"""
from __future__ import annotations


def anchored(items, last_key, base_sum):
    """Yield (payload, value, cumulative) for new cumulative-statistic rows.

    items:    iterable of (sort_key, value, payload). sort_key must be
              orderable and unique per bucket.
    last_key: highest sort_key already stored in the recorder, or None for a
              fresh series. Items with sort_key <= last_key are skipped
              (already imported) so historical sums are never rewritten.
    base_sum: cumulative sum of the last stored row (the recorder baseline).

    The cumulative continues from base_sum, so the emitted series joins the
    stored one without a regression -> no negative bar on the Energy
    dashboard, even across HA restarts when the API only returns a recent
    window (the original cause of the -3416 kWh spike).

    ponytail: revised values for already-stored buckets are ignored (Linky
    daily values rarely change after a couple of days). Re-import the whole
    series by hand if a correction ever matters.
    """
    cumulative = base_sum
    for sort_key, value, payload in sorted(items, key=lambda it: it[0]):
        if last_key is not None and sort_key <= last_key:
            continue
        cumulative += value
        yield payload, value, cumulative
