"""Runnable check for the cumulative-anchoring logic.

    python tests/test_anchored.py

Guards the fix for the -3416 kWh negative-bar bug: across an HA restart the
API only returns a recent window, so the cumulative series must continue from
the recorder's stored baseline instead of restarting from zero.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "vialis"))

from _stats import anchored  # noqa: E402


def emit(items, last_key, base):
    return list(anchored(items, last_key, base))


items = [(1, 5.0, "a"), (2, 3.0, "b"), (3, 2.0, "c")]

# fresh series (no prior data) -> plain cumulative from 0
assert [c for _, _, c in emit(items, None, 0.0)] == [5.0, 8.0, 10.0]

# restart: recorder already has up to key=2 at sum=8.0. Only key 3 is appended
# and it continues from 8.0 -> 10.0, exactly matching the fresh series. No seam.
assert emit(items, 2, 8.0) == [("c", 2.0, 10.0)]

# the bug being fixed: re-importing from 0 after losing in-memory history makes
# the first row 5.0, far below the stored 8.0 -> negative dashboard bar.
assert emit(items, None, 0.0)[0][2] == 5.0  # what we must NOT do across restarts

# monotonic and never regresses below the baseline for positive values
out = emit([(1, 1.0, 1), (2, 1.0, 2), (3, 1.0, 3)], 1, 100.0)
assert [c for *_, c in out] == [101.0, 102.0]
assert all(c > 100.0 for *_, c in out)

# everything already stored -> nothing emitted
assert emit(items, 99, 50.0) == []

# unsorted input is still emitted in key order
assert [p for p, _, _ in emit([(3, 2.0, "c"), (1, 5.0, "a"), (2, 3.0, "b")], None, 0.0)] == ["a", "b", "c"]

print("ok")
