"""
AN-37739 step 1 (updated for AN-37924): how much vehicle_cluster_history data exists, and -
since it's cluster-keyed, not driver-keyed - how much of it resolves to real drivers.

Raw SQL against vehicle_cluster_history: this checkout's FaceRecognition branch predates
AN-37924's merge to master, so frs_src.orm has no VehicleClusterHistory model yet (see
README.md). cluster_details IS available (unchanged by AN-37924), used via frs_src.orm for the
cluster->driver join.

Read-only. Requires frs_src importable and DB connectivity/credentials configured. Not run yet.
"""
from collections import defaultdict
from typing import Dict, Iterable, List

import pandas as pd
import sqlalchemy as sa

from frs_src import orm

from an37739.candidate_set import merge_driver_counts
from an37739.cluster_resolution import resolve_cluster_counts

_VEHICLE_WEEKLY_CLUSTER_COUNTS_SQL = sa.text(
    "SELECT vehicle_id, week_start, cluster_counts FROM vehicle_cluster_history "
    "WHERE tenant_id = :tenant_id AND week_start = ANY(:week_starts)"
)


def enabled_tenant_ids(session) -> List[int]:
    """Tenants that actually have vehicle_cluster_history rows.

    Same reasoning as the old vehicle_driver_history version: the sync job only ever writes
    for a tenant with cfg.VEHICLE_DRIVER_HISTORY.enabled True - AN-37924 reused that flag name
    rather than introducing VEHICLE_CLUSTER_HISTORY, so "has any row" still means "is enabled".
    """
    rows = session.execute(sa.text("SELECT DISTINCT tenant_id FROM vehicle_cluster_history")).all()
    return sorted(tenant_id for (tenant_id,) in rows)


def coverage_summary(session) -> pd.DataFrame:
    """Per enabled tenant: how many vehicles, how many distinct weeks, and the min/max
    week_start currently in vehicle_cluster_history."""
    rows = session.execute(
        sa.text(
            "SELECT tenant_id, count(DISTINCT vehicle_id) AS vehicles, "
            "count(DISTINCT week_start) AS weeks, min(week_start) AS earliest_week, "
            "max(week_start) AS latest_week FROM vehicle_cluster_history GROUP BY tenant_id"
        )
    ).all()
    return pd.DataFrame(
        rows, columns=["tenant_id", "vehicles", "weeks", "earliest_week", "latest_week"]
    )


def fetch_vehicle_weekly_cluster_counts(
    session, tenant_id: int, week_starts: List
) -> Dict[int, Dict[object, dict]]:
    """{vehicle_id: {week_start: cluster_counts}} for one tenant, restricted to `week_starts` -
    week-indexed (not a plain list) so a caller can safely pick out a sub-window later without
    assuming row order or that every vehicle has a row for every requested week."""
    rows = session.execute(
        _VEHICLE_WEEKLY_CLUSTER_COUNTS_SQL,
        {"tenant_id": tenant_id, "week_starts": list(week_starts)},
    ).all()
    vehicle_weekly_counts: Dict[int, Dict[object, dict]] = defaultdict(dict)
    for vehicle_id, week_start, cluster_counts in rows:
        vehicle_weekly_counts[vehicle_id][week_start] = cluster_counts or {}
    return dict(vehicle_weekly_counts)


def fetch_cluster_to_driver(session, cluster_ids: Iterable[int]) -> Dict[int, int]:
    """{cluster_id: driver_id} for every cluster_id in `cluster_ids` still present in
    cluster_details. A cluster_id absent from the result was hard-deleted/not found -
    an37739.cluster_resolution.resolve_cluster_counts treats that the same as a deleted
    driver_id (drops it)."""
    rows = (
        session.query(orm.ClusterDetails.cluster_id, orm.ClusterDetails.driver_id)
        .filter(orm.ClusterDetails.cluster_id.in_(list(cluster_ids)))
        .all()
    )
    return dict(rows)


def fetch_vehicle_weekly_driver_counts(
    session, tenant_id: int, week_starts: List
) -> Dict[int, Dict[object, dict]]:
    """fetch_vehicle_weekly_cluster_counts, with the cluster->driver join already applied -
    {vehicle_id: {week_start: driver_counts}}, the shape an37739.candidate_set's (unchanged,
    tested) logic expects. Resolves every distinct cluster_id seen across the whole
    tenant/window in one extra query, rather than one cluster_details lookup per vehicle/week.
    """
    vehicle_weekly_cluster_counts = fetch_vehicle_weekly_cluster_counts(
        session, tenant_id, week_starts
    )
    all_cluster_ids = {
        int(cluster_id)
        for weekly in vehicle_weekly_cluster_counts.values()
        for cluster_counts in weekly.values()
        for cluster_id in cluster_counts
    }
    cluster_to_driver = fetch_cluster_to_driver(session, all_cluster_ids)
    return {
        vehicle_id: {
            week: resolve_cluster_counts(cluster_counts, cluster_to_driver)
            for week, cluster_counts in weekly.items()
        }
        for vehicle_id, weekly in vehicle_weekly_cluster_counts.items()
    }


def driver_count_distribution(
    vehicle_weekly_driver_counts: Dict[int, Dict[object, dict]]
) -> pd.Series:
    """How many vehicles have 1 / 2 / 3+ distinct REAL drivers over the window - the coverage
    question the ticket's step 1 asks for directly.

    Takes already-resolved driver counts (fetch_vehicle_weekly_driver_counts), NOT raw cluster
    counts: distinct cluster_id count is a different question (one driver can have several
    clusters within threshold) and would overstate how many vehicles look multi-driver.
    """

    def bucket(n_drivers: int) -> str:
        return str(n_drivers) if n_drivers < 3 else "3+"

    counts = [
        bucket(len(merge_driver_counts(weekly.values())))
        for weekly in vehicle_weekly_driver_counts.values()
    ]
    return pd.Series(counts).value_counts().sort_index()
