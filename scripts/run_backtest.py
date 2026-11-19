"""CLI: AN-37739 step 3 - for one tenant, a target ISO week, and every cutoff in the default
sweep: build the candidate single-driver-vehicle set from the lookback window, compute the
target week's actual per-vehicle stats via the production vls-kpi logic (read-only, see
offline_scripts/an_37739_assignment_rate_baseline.py), and report the projected
assignment-rate gain plus the free (no-labelling) contradiction count per cutoff.

Not run yet - depends on run_candidate_sweep.py's DB access and the baseline script's IDMS
access both being confirmed first.
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from frs_src.config import cfg

from offline_scripts.an_37739_assignment_rate_baseline import aggregate_vehicle_week
from frs_src.vls_kpi import VLSKpis

from an37739.backtest import project_gain
from an37739.candidate_set import build_candidate_drivers
from an37739.config import default_cutoff_sweep
from an37739.coverage import fetch_vehicle_weekly_driver_counts
from an37739.precision import contradicted_vehicles
from run_candidate_sweep import lookback_week_starts


def target_week_vehicle_stats(tenant_id: int, year: int, week: int) -> dict:
    """{vehicle_id: aggregated weekly out_dict} for the target week, via the exact same
    read-only path as the baseline script (VLSKpis.process_tenant - no DB writes)."""
    kpis = VLSKpis()
    kpis.tenant_id = tenant_id
    vehicles = kpis.idms_db_utils.get_tenant_vehicles(tenant_id)

    monday = datetime.datetime.strptime(f"{year}-W{week}-1", "%G-W%V-%u")
    dates = [str(monday + datetime.timedelta(days=offset)).split(" ")[0] for offset in range(7)]

    per_vehicle_days = {}
    for date in dates:
        for vehicle_dict in kpis.process_tenant(date, vehicles):
            per_vehicle_days.setdefault(vehicle_dict["vehicle_id"], []).append(vehicle_dict)

    return {
        vehicle_id: aggregate_vehicle_week(days) for vehicle_id, days in per_vehicle_days.items()
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant_id", type=int, required=True)
    parser.add_argument("--target_year", type=int, required=True)
    parser.add_argument("--target_week", type=int, required=True, help="ISO week number")
    args = parser.parse_args()

    target_monday = datetime.datetime.strptime(
        f"{args.target_year}-W{args.target_week}-1", "%G-W%V-%u"
    ).date()

    print("Computing target-week vehicle stats (read-only, no DB writes)...")
    vehicle_weekly_stats = target_week_vehicle_stats(
        args.tenant_id, args.target_year, args.target_week
    )
    target_week_distribution = {
        vehicle_id: stats["vls_driver_distribution"]
        for vehicle_id, stats in vehicle_weekly_stats.items()
    }

    session = cfg.SQLALCHEMY.session()
    try:
        cutoffs = default_cutoff_sweep()
        max_lookback = max(c.lookback_weeks for c in cutoffs)
        all_lookback_counts = fetch_vehicle_weekly_driver_counts(
            session, args.tenant_id, lookback_week_starts(target_monday, max_lookback)
        )

        for cutoff in cutoffs:
            window = set(lookback_week_starts(target_monday, cutoff.lookback_weeks))
            scoped = {
                vehicle_id: [wc for week, wc in weekly.items() if week in window]
                for vehicle_id, weekly in all_lookback_counts.items()
            }
            candidates = build_candidate_drivers(scoped, cutoff)
            gain = project_gain(vehicle_weekly_stats, candidates)
            contradictions = contradicted_vehicles(candidates, target_week_distribution)

            print(
                f"{cutoff.label}: {gain['vehicles']} vehicle(s), "
                f"baseline {gain['baseline_rate_pct']}% -> projected {gain['projected_rate_pct']}% "
                f"(gain {gain['gain_pct']} pp), "
                f"{len(contradictions)}/{gain['vehicles']} contradicted this week"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
