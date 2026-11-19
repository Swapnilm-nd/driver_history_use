# AN-37739: single-driver-vehicle assignment-rate gain

Analysis spike: how much would the VLS assignment-rate KPI improve if vehicles with
essentially one recognized driver (per `vehicle_driver_history`) had their currently
unassigned drive minutes auto-assigned to that one driver - and at what precision cost.

Jira: https://netradyne.atlassian.net/browse/AN-37739
FaceRecognition repo (source of `frs_src`/`offline_scripts`):
https://github.com/netradyne/FaceRecognition

## Layout

```
src/an37739/
  config.py       cutoff sweep (lookback weeks x min images x min share %)
  candidate_set.py  pure logic: driver_counts -> {vehicle_id: driver_id}. No DB/AWS imports.
  cluster_resolution.py  pure logic: cluster_counts + {cluster_id: driver_id} -> driver_counts.
                  No DB/AWS imports. See "AN-37924" section below - this is new, not optional.
  coverage.py     step 1 - how much vehicle_cluster_history data exists, resolved to drivers.
                  Needs frs_src + DB.
  backtest.py     step 3 - projected assignment-rate gain. Needs frs_src-shaped input dicts
                  but no DB import itself (pure over vehicle_weekly_stats dicts) - unaffected
                  by AN-37924, since it reads IDMS minute/DIS data, not vehicle_*_history.
  precision.py    step 4 - free contradiction check + labelling-tool sample selection.
scripts/          CLI entrypoints wiring the above to the real DB (not run yet)
tests/            unit tests for candidate_set.py + cluster_resolution.py - these DO run,
                  no DB/AWS needed
```

## AN-37924: vehicle_driver_history was replaced mid-stream

`vehicle_driver_history` (AN-36696, what this project originally targeted) was dropped from
production on 2026-11-04 and replaced by **`vehicle_cluster_history`** (AN-37924), keyed by
`cluster_id -> image_count` instead of `driver_id -> image_count`. Confirmed live in prod:
209,047 rows as of 2026-11-11, e.g. tenant 24454 / vehicle 1787524 / week 2026-11-02 ->
`{"2295971": 110, "3020910": 10, "4027857": 122}`.

Nothing in the FaceRecognition repo joins cluster_id back to driver_id - that's left to the
reader by design (per that repo's `claude_workspace/design/AN-37924.md`). `cluster_resolution.py`
is that join. Important caveat carried over from that design doc: for a labelled driver with
multiple clusters, only the image's *closest* cluster is counted, so a multi-cluster driver's
resolved count under-represents their true recognition frequency relative to the old
driver-keyed scheme - a data property, not something this join can correct for.

This checkout's FaceRecognition branch predates AN-37924's merge to master, so `frs_src.orm`
has no `VehicleClusterHistory` model here yet - `coverage.py` queries the table with raw SQL
instead of the ORM to avoid depending on that. `cluster_details` (used for the join) is
unchanged by AN-37924 and already available via `frs_src.orm.ClusterDetails`.

## What's safe to run right now

`tests/` and `src/an37739/candidate_set.py` have zero dependencies beyond the stdlib:

```bash
python3 -m unittest discover -s tests -v
```

Everything else (`coverage.py`, `backtest.py`'s real inputs, every `scripts/*.py`) imports
`frs_src`/`offline_scripts` from the FaceRecognition repo and talks to the real FRS/IDMS
Postgres DBs - needs:

```bash
pip install -e /Users/swapnilmane/NetraDyne/FaceRecognition/.claude/worktrees/system-design-implementation-62aaf6
```

plus the DB/AWS credentials that repo's own `offline_scripts/` already require. None of
`scripts/*.py` have been run yet - confirm DB/AWS access first (see
`offline_scripts/an_37739_assignment_rate_baseline.py` in the FaceRecognition repo for the
original read-only connectivity check).

## Pipeline (matches the ticket's 5 deliverables)

1. **Coverage** - `scripts/run_coverage.py`: which tenants have `vehicle_driver_history` data,
   how many weeks, vehicle count. (`an37739.coverage.coverage_summary`)
2. **Candidate set / "single driver" definition** - `scripts/run_candidate_sweep.py`: for a
   tenant + target week, how many vehicles qualify under each cutoff in the sweep.
   (`an37739.candidate_set.build_candidate_drivers`)
3. **Backtest** - `scripts/run_backtest.py`: projected `vls_assigned_minutes` gain per cutoff,
   reusing the production `ComputeVehicle`/`VLSKpis` read-only (no reimplementation of its
   per-minute logic - see `an37739/backtest.py`'s docstring for why that's safe).
4. **Precision** - `an37739.precision.contradicted_vehicles` (free) and `sample_for_labelling`
   (feeds a labelling-tool task generator - not built here, see that module's docstring).
5. **Compare & report** - not yet built; once 1-4 run for real data, write up gain-vs-precision
   per cutoff against https://netradyne.atlassian.net/browse/AN-36281's 0.8%/2.8% estimate.

## Key facts this design relies on (traced in the FaceRecognition repo)

- `VehicleDriverHistory` ORM: `frs_src/orm.py:977` - one row per `(tenant_id, vehicle_id,
  week_start)`, `driver_counts` JSONB `{driver_id: image_count}`.
- The real assignment-rate KPI is **minute-based**, not image-count-based:
  `vls_assigned_minutes = sum(vls_driver_id > 0)` per minute, where `vls_driver_id` comes from
  IDMS's `ndfaceimage.driver_id` joined onto each DIS's minute window - see
  `frs_src/vls_kpi.py:1042` (`ComputeVehicle.compute_vehicle`).
- `vls_driver_distribution` (`{driver_id: minute_count}`, including non-positive "unassigned"
  codes) is already computed there and is the only extra field
  `offline_scripts/an_37739_assignment_rate_baseline.py`'s `aggregate_vehicle_week` had to
  start carrying through for `an37739.backtest` to work without touching `ComputeVehicle`.
# driver_history_use
