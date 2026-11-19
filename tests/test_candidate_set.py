import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from an37739.candidate_set import (
    build_candidate_drivers,
    dominant_driver,
    is_single_driver_vehicle,
    merge_driver_counts,
)
from an37739.config import Cutoff

CUTOFF = Cutoff(lookback_weeks=4, min_images=10, min_share_pct=90.0)


class MergeDriverCountsTest(unittest.TestCase):
    def test_sums_across_weeks_and_normalises_keys_to_int(self):
        weekly = [{"101": 5, "202": 1}, {"101": 7}]
        self.assertEqual(merge_driver_counts(weekly), {101: 12, 202: 1})

    def test_empty_input_gives_empty_dict(self):
        self.assertEqual(merge_driver_counts([]), {})


class DominantDriverTest(unittest.TestCase):
    def test_picks_highest_count(self):
        driver_id, dominant_count, total, share_pct = dominant_driver({101: 12, 202: 1})
        self.assertEqual((driver_id, dominant_count, total), (101, 12, 13))
        self.assertAlmostEqual(share_pct, 12 / 13 * 100)

    def test_ties_broken_by_lowest_driver_id(self):
        driver_id, _, _, _ = dominant_driver({202: 5, 101: 5})
        self.assertEqual(driver_id, 101)

    def test_empty_gives_none(self):
        self.assertIsNone(dominant_driver({}))


class IsSingleDriverVehicleTest(unittest.TestCase):
    def test_passes_when_both_thresholds_met(self):
        # 12 images >= min_images(10), share 12/13=92.3% >= min_share_pct(90%)
        self.assertTrue(is_single_driver_vehicle({101: 12, 202: 1}, CUTOFF))

    def test_fails_below_min_images(self):
        self.assertFalse(is_single_driver_vehicle({101: 9}, CUTOFF))

    def test_fails_below_min_share(self):
        # 10 images meets min_images, but share 10/15=66.7% < min_share_pct(90%)
        self.assertFalse(is_single_driver_vehicle({101: 10, 202: 5}, CUTOFF))

    def test_empty_history_never_qualifies(self):
        self.assertFalse(is_single_driver_vehicle({}, CUTOFF))


class BuildCandidateDriversTest(unittest.TestCase):
    def test_only_qualifying_vehicles_included(self):
        vehicle_weekly_counts = {
            1001: [{"101": 12}],  # single driver, qualifies
            1002: [{"101": 6}, {"202": 6}],  # 50/50 split, doesn't qualify
            1003: [{"303": 3}],  # below min_images
        }
        self.assertEqual(
            build_candidate_drivers(vehicle_weekly_counts, CUTOFF), {1001: 101}
        )


if __name__ == "__main__":
    unittest.main()
