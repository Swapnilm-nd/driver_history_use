"""CLI: AN-37739 step 2 - for one tenant and target ISO week, build the candidate single-
driver-vehicle set for every cutoff in the default sweep, and report how many vehicles
qualify under each. Read-only. Not run yet.
"""
import argparse
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from frs_src.config import cfg

from an37739.candidate_set import build_candidate_drivers
from an37739.config import default_cutoff_sweep
from an37739.coverage import fetch_vehicle_weekly_driver_counts


def lookback_week_starts(target_monday: datetime.date, lookback_weeks: int) -> list:
    """The `lookback_weeks` Mondays strictly before `target_monday` (week W) - weeks
    W-1 ... W-lookback_weeks, matching the ticket's "weeks W-K...W-1" backtest window."""
    return [target_monday - datetime.timedelta(weeks=n) for n in range(1, lookback_weeks + 1)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant_id", type=int, required=True)
    parser.add_argument(
        "--target_monday", required=True, help="YYYY-MM-DD, Monday of target week W"
    )
    args = parser.parse_args()
    target_monday = datetime.datetime.strptime(args.target_monday, "%Y-%m-%d").date()

    session = cfg.SQLALCHEMY.session()
    try:
        cutoffs = default_cutoff_sweep()
        max_lookback = max(c.lookback_weeks for c in cutoffs)
        # One query sized for the widest lookback any cutoff needs (plus the cluster->driver
        # join it requires); each cutoff below just picks its own sub-window out of the same
        # already-resolved driver counts.
        vehicle_weekly_counts = fetch_vehicle_weekly_driver_counts(
            session, args.tenant_id, lookback_week_starts(target_monday, max_lookback)
        )

        for cutoff in cutoffs:
            window = set(lookback_week_starts(target_monday, cutoff.lookback_weeks))
            scoped = {
                vehicle_id: [wc for week, wc in weekly.items() if week in window]
                for vehicle_id, weekly in vehicle_weekly_counts.items()
            }
            candidates = build_candidate_drivers(scoped, cutoff)
            print(
                f"{cutoff.label}: {len(candidates)} qualifying vehicle(s) "
                f"/ {len(vehicle_weekly_counts)} total"
            )
    finally:
        session.close()


if __name__ == "__main__":
    main()
