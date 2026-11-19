"""
Resolving vehicle_cluster_history's cluster-keyed counts (AN-37924) back to driver-keyed
counts - the shape candidate_set.py's (unchanged, already-tested) logic expects.

vehicle_cluster_history replaced vehicle_driver_history on 2026-11-04 (AN-37924): the table is
now {cluster_id: image_count} instead of {driver_id: image_count}, and nothing in frs_src joins
cluster_id back to a driver_id - that's left to the reader (per
claude_workspace/design/AN-37924.md in the FaceRecognition repo). This module is that join,
done as pure logic over an already-fetched {cluster_id: driver_id} mapping, so it stays
DB-free and unit-testable, same as candidate_set.py.

Known limitation (same design doc): for a labelled driver with multiple clusters, only the
CLOSEST cluster to a given image is counted - so a multi-cluster driver's resolved count is an
undercount of their true recognition frequency relative to the old driver-keyed scheme. Nothing
here corrects for that; it's a property of the upstream data, not a bug in this join.
"""
from typing import Dict

# cluster_details.driver_id sentinels (same conventions as frs_src/assignment_rate_tool/app.py
# in the FaceRecognition repo) - none of these is a resolved driver identity, so none can be a
# "candidate driver" for auto-assignment.
UNLABELLED_DRIVER_ID = -1
DELETED_DRIVER_ID = -5
NOT_MY_DRIVER_ID = 0


def resolve_cluster_counts(
    cluster_counts: Dict[str, int], cluster_to_driver: Dict[int, int]
) -> Dict[int, int]:
    """One week's {cluster_id: image_count} -> {driver_id: image_count}, keeping only clusters
    that resolve to a real (positive) driver_id. A cluster_id absent from `cluster_to_driver`
    (e.g. hard-deleted, not in cluster_details at all) is dropped, same as an explicit
    DELETED_DRIVER_ID would be.

    Several cluster_ids can resolve to the same driver_id (a driver with multiple clusters) -
    their counts are summed, which is what lets candidate_set.merge_driver_counts /
    dominant_driver treat the result exactly like the old driver-keyed vehicle_driver_history
    rows, with no further changes needed downstream.
    """
    driver_counts: Dict[int, int] = {}
    for cluster_id_str, count in cluster_counts.items():
        driver_id = cluster_to_driver.get(int(cluster_id_str))
        if driver_id is None or driver_id <= 0:  # missing, unlabelled, deleted, not-my-driver
            continue
        driver_counts[driver_id] = driver_counts.get(driver_id, 0) + int(count)
    return driver_counts
