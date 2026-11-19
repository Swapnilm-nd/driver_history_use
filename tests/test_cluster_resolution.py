import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from an37739.cluster_resolution import resolve_cluster_counts


class ResolveClusterCountsTest(unittest.TestCase):
    def test_sums_multiple_clusters_for_the_same_driver(self):
        cluster_counts = {"111": 110, "222": 10, "333": 122}
        cluster_to_driver = {111: 5001, 222: 5001, 333: 5002}
        self.assertEqual(
            resolve_cluster_counts(cluster_counts, cluster_to_driver), {5001: 120, 5002: 122}
        )

    def test_drops_unlabelled_deleted_and_not_my_driver_clusters(self):
        cluster_counts = {"1": 10, "2": 20, "3": 30, "4": 40}
        cluster_to_driver = {1: -1, 2: -5, 3: 0, 4: 5001}
        self.assertEqual(resolve_cluster_counts(cluster_counts, cluster_to_driver), {5001: 40})

    def test_drops_clusters_missing_from_the_mapping(self):
        cluster_counts = {"1": 10, "2": 20}
        cluster_to_driver = {1: 5001}  # cluster 2 not found (e.g. hard-deleted)
        self.assertEqual(resolve_cluster_counts(cluster_counts, cluster_to_driver), {5001: 10})

    def test_empty_input_gives_empty_dict(self):
        self.assertEqual(resolve_cluster_counts({}, {}), {})


if __name__ == "__main__":
    unittest.main()
