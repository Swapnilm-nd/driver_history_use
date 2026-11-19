"""
Pure, DB-free logic for turning vehicle_driver_history data into a per-vehicle "single
confident driver" verdict (AN-37739). No frs_src/DB imports - unit-tested directly in
tests/test_candidate_set.py with no DB or AWS access required.

Input shape: for one vehicle, a list of that vehicle's weekly VehicleDriverHistory rows'
`driver_counts` JSONB values - i.e. one dict per ISO week in the lookback window, each
`{driver_id (str, since JSONB keys are always strings): image_count}`.
"""
from typing import Dict, Iterable, List, Optional, Tuple

from an37739.config import Cutoff

DriverCounts = Dict[int, int]


def merge_driver_counts(weekly_driver_counts: Iterable[Dict[str, int]]) -> DriverCounts:
    """Sum driver image counts across weeks for one vehicle, normalising JSONB's string keys
    back to int driver ids so every downstream function deals in one type."""
    merged: DriverCounts = {}
    for week_counts in weekly_driver_counts:
        for driver_id_str, count in week_counts.items():
            driver_id = int(driver_id_str)
            merged[driver_id] = merged.get(driver_id, 0) + int(count)
    return merged


def dominant_driver(driver_counts: DriverCounts) -> Optional[Tuple[int, int, int, float]]:
    """(driver_id, dominant_count, total_count, share_pct) for the vehicle's top driver by
    image count, or None if driver_counts is empty. Ties broken by lowest driver_id, so the
    result is deterministic across reruns."""
    if not driver_counts:
        return None
    total = sum(driver_counts.values())
    driver_id = max(driver_counts, key=lambda d: (driver_counts[d], -d))
    dominant_count = driver_counts[driver_id]
    share_pct = (dominant_count / total) * 100 if total else 0.0
    return driver_id, dominant_count, total, share_pct


def is_single_driver_vehicle(driver_counts: DriverCounts, cutoff: Cutoff) -> bool:
    """Whether this vehicle's history is confident enough, under `cutoff`, to say it has
    exactly one real driver. A vehicle with zero recognized drivers never qualifies - there's
    no candidate to assign its unassigned DIS to."""
    result = dominant_driver(driver_counts)
    if result is None:
        return False
    _driver_id, dominant_count, _total, share_pct = result
    return dominant_count >= cutoff.min_images and share_pct >= cutoff.min_share_pct


def build_candidate_drivers(
    vehicle_weekly_counts: Dict[int, List[Dict[str, int]]], cutoff: Cutoff
) -> Dict[int, int]:
    """{vehicle_id: driver_id} for every vehicle that qualifies as single-driver under
    `cutoff`, given each vehicle's weekly driver_counts dicts. Callers (an37739.coverage)
    decide which weeks belong in the lookback window - this function only sums whatever it's
    handed, it has no notion of ISO weeks itself."""
    candidates: Dict[int, int] = {}
    for vehicle_id, weekly_counts in vehicle_weekly_counts.items():
        merged = merge_driver_counts(weekly_counts)
        if is_single_driver_vehicle(merged, cutoff):
            driver_id, _dominant_count, _total, _share_pct = dominant_driver(merged)
            candidates[vehicle_id] = driver_id
    return candidates
