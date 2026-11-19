"""
AN-37739 step 3: projected assignment-rate gain from auto-assigning single-driver vehicles'
currently-unassigned minutes to their one known driver.

Deliberately does NOT reimplement or monkeypatch ComputeVehicle's internals. A vehicle's
`vls_driver_distribution` (from VLSKpis.process_tenant -> ComputeVehicle.compute_vehicle, see
frs_src/vls_kpi.py:1127, and now carried through
offline_scripts/an_37739_assignment_rate_baseline.aggregate_vehicle_week) already gives
{driver_id: minute_count} for every minute in the window, including the non-positive
"unassigned"/"not-my-driver" buckets. For a vehicle in the candidate set, this ticket's rule
credits ALL of those non-positive-driver minutes to the one known driver - so the projection
is just that distribution's non-positive total added to vls_assigned_minutes. Same "credit the
whole gap" shape as the existing Assignment Rate Debug Tool's recall projections
(frs_src/assignment_rate_tool/app.py, _recall_projections) use for a related question.
"""
from dataclasses import dataclass
from typing import Dict, List


@dataclass
class VehicleProjection:
    vehicle_id: int
    driving_minutes: int
    baseline_assigned_minutes: int
    unassigned_minutes: int
    candidate_driver_id: int
    projected_assigned_minutes: int


def unassigned_minutes(vls_driver_distribution: Dict[int, int]) -> int:
    """Minutes whose vls_driver_id is <= 0 (unassigned, not-my-driver, etc) - the pool this
    ticket's rule would redirect to the vehicle's one known driver."""
    return sum(count for driver_id, count in vls_driver_distribution.items() if driver_id <= 0)


def project_vehicle(vehicle_weekly_stats: dict, candidate_driver_id: int) -> VehicleProjection:
    """vehicle_weekly_stats: one vehicle's aggregated weekly dict, as produced by
    offline_scripts.an_37739_assignment_rate_baseline.aggregate_vehicle_week (must include
    vls_driver_distribution)."""
    gap = unassigned_minutes(vehicle_weekly_stats["vls_driver_distribution"])
    assigned = vehicle_weekly_stats["vls_assigned_minutes"]
    return VehicleProjection(
        vehicle_id=vehicle_weekly_stats["vehicle_id"],
        driving_minutes=vehicle_weekly_stats["driving_minutes"],
        baseline_assigned_minutes=assigned,
        unassigned_minutes=gap,
        candidate_driver_id=candidate_driver_id,
        projected_assigned_minutes=assigned + gap,
    )


def project_gain(
    vehicle_weekly_stats_by_id: Dict[int, dict], candidate_drivers: Dict[int, int]
) -> dict:
    """Baseline vs. projected assignment rate across every vehicle in `candidate_drivers`,
    rolled up - plus the per-vehicle detail, for inspection/export into the final report.

    Vehicles in `candidate_drivers` that have no entry in `vehicle_weekly_stats_by_id` are
    silently skipped (the candidate set is built from the lookback window and this dict from
    the target week - a vehicle with no driving minutes in the target week itself isn't an
    error, there's just nothing to project a gain onto).
    """
    projections: List[VehicleProjection] = [
        project_vehicle(vehicle_weekly_stats_by_id[vehicle_id], driver_id)
        for vehicle_id, driver_id in candidate_drivers.items()
        if vehicle_id in vehicle_weekly_stats_by_id
    ]
    driving_minutes = sum(p.driving_minutes for p in projections)
    baseline_assigned = sum(p.baseline_assigned_minutes for p in projections)
    projected_assigned = sum(p.projected_assigned_minutes for p in projections)
    return {
        "vehicles": len(projections),
        "driving_minutes": driving_minutes,
        "baseline_assigned_minutes": baseline_assigned,
        "projected_assigned_minutes": projected_assigned,
        "baseline_rate_pct": (
            round(baseline_assigned / driving_minutes * 100, 3) if driving_minutes else None
        ),
        "projected_rate_pct": (
            round(projected_assigned / driving_minutes * 100, 3) if driving_minutes else None
        ),
        "gain_pct": (
            round((projected_assigned - baseline_assigned) / driving_minutes * 100, 3)
            if driving_minutes
            else None
        ),
        "per_vehicle": projections,
    }
