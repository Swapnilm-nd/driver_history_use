# AN-37739: Step-by-Step Record

Jira: https://netradyne.atlassian.net/browse/AN-37739

Fresh record, restarted 2026-11-18. Each step below follows a fixed template: Task, DB Used,
Tables Used, Commands used, Output Table, Observation.

---

## Step 1: Coverage — Driver-Diversity Distribution

**Task:** Across all enabled tenants, all weeks combined, count how many vehicles show 1, 2, or
3+ distinct recognized drivers — confirms the ticket's premise has real volume, and surfaces any
data-availability constraints before investing in later steps.

**DB Used:** FRS

**Tables Used:** `vehicle_cluster_history`, `cluster_details`

**Commands used:**
```sql
WITH vehicle_cluster_counts AS (
    SELECT tenant_id, vehicle_id, kv.key::bigint AS cluster_id, kv.value::int AS images
    FROM vehicle_cluster_history
    CROSS JOIN LATERAL jsonb_each_text(cluster_counts) AS kv(key, value)
),
vehicle_driver_counts AS (
    SELECT vcc.tenant_id, vcc.vehicle_id, cd.driver_id, SUM(vcc.images) AS images
    FROM vehicle_cluster_counts vcc
    JOIN cluster_details cd ON cd.cluster_id = vcc.cluster_id
    WHERE cd.driver_id > 0
    GROUP BY vcc.tenant_id, vcc.vehicle_id, cd.driver_id
),
vehicle_driver_diversity AS (
    SELECT tenant_id, vehicle_id, COUNT(DISTINCT driver_id) AS n_drivers
    FROM vehicle_driver_counts
    GROUP BY tenant_id, vehicle_id
)
SELECT
    CASE WHEN n_drivers >= 3 THEN '3+' ELSE n_drivers::text END AS driver_bucket,
    count(*) AS vehicles,
    round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_vehicles
FROM vehicle_driver_diversity
GROUP BY driver_bucket
ORDER BY driver_bucket;
```

**How the final SELECT works:**

1. `CASE WHEN n_drivers >= 3 THEN '3+' ELSE n_drivers::text END AS driver_bucket` — runs per
   vehicle, before any grouping. Converts that vehicle's `n_drivers` number into a label: 3 or
   more collapses to `'3+'` (merging 3, 4, 5... into one bucket); otherwise the number itself
   becomes text (`'1'` or `'2'`). The `::text` cast is needed so a number and a string like
   `'3+'` can share one output column.
2. `count(*) AS vehicles` — once `GROUP BY driver_bucket` collapses every vehicle sharing the
   same label into one group, this counts how many vehicles landed in each bucket.
3. `round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_vehicles` — a window function.
   `sum(count(*)) OVER ()` computes the grand total across *every* bucket without collapsing the
   result to one row, so that same grand total gets attached to each bucket's own row. Each
   bucket then divides its own count by that shared total — giving every bucket's percentage
   share in one pass, no second query needed.

   Worked example with 5 vehicles (`n_drivers` = 1, 1, 2, 3, 5 → buckets `'1','1','2','3+','3+'`):

   | driver_bucket | vehicles | pct_of_vehicles |
   |---|---|---|
   | 1 | 2 | 40.0 |
   | 2 | 1 | 20.0 |
   | 3+ | 2 | 40.0 |

**Output Table:**

| driver_bucket | vehicles | pct_of_vehicles |
|---|---|---|
| 1 | 25,941 | 30.2 |
| 2 | 16,018 | 18.6 |
| 3+ | 43,969 | 51.2 |

(Total: 85,928 vehicles — close to, but not exactly, Step 2's 86,125; the two queries were run a
few minutes apart on continuously-updating data, so a small drift between them is expected.)

**Which weeks "all weeks combined" actually covers** — Step 1 has no week filter, so it's worth
knowing exactly what date range that spans. Query:
```sql
SELECT week_start, count(*) AS vehicle_rows
FROM vehicle_cluster_history
GROUP BY week_start
ORDER BY week_start;
```

| week_start (Monday) | ISO week | vehicle_rows | Status |
|---|---|---|---|
| 2026-10-26 | Week 43, 2026 | 93 | Negligible — startup trickle, before the feature was fully live |
| 2026-11-02 | Week 44, 2026 | 109,821 | Complete — Step 3's history/lookback week |
| 2026-11-09 | Week 45, 2026 | 120,132 | Complete — Step 3/4's test week |
| 2026-11-16 | Week 46, 2026 | 107,105 | In progress — only 3 days elapsed as of 2026-11-18 |

So Step 1's scope is ISO weeks 43–46, 2026 (2026-10-26 through 2026-11-22, with week 46 not yet
finished) — not an indefinitely growing window, just these four weeks' worth of data as of today.

**Observation:** A dramatic shift from the original 2026-11-12 run (1 driver: 47.7%, 2: 22.9%,
3+: 29.4%). Now **"exactly 1 driver" has dropped to 30.2%** (was 47.7%) and **"3+ drivers" has
jumped to 51.2%** (was 29.4%) — more than half the fleet now shows 3 or more distinct recognized
drivers, where under a third did six days ago. This is the same effect flagged in Step 2's
observation, but far starker here: with more weeks of history accumulated, many vehicles that
looked single- or dual-driver on a short window are now revealing additional drivers that simply
hadn't been recognized yet. This directly confirms the ticket's own stated risk and suggests the
raw 1/2/3+ split is highly sensitive to how much lookback history is available — not a stable
fleet characteristic on short windows.

---

## Step 2: Define "Single Driver" — Cutoff Sweep

**Task:** Sweep seven candidate `(min_images, min_share_pct)` cutoffs against the same vehicle
population, to see how many vehicles qualify as "single driver" under each threshold.

**DB Used:** FRS

**Tables Used:** `vehicle_cluster_history`, `cluster_details`

**Commands used:**
```sql
WITH vehicle_cluster_counts AS (
    SELECT tenant_id, vehicle_id, kv.key::bigint AS cluster_id, kv.value::int AS images
    FROM vehicle_cluster_history
    CROSS JOIN LATERAL jsonb_each_text(cluster_counts) AS kv(key, value)
),
vehicle_driver_counts AS (
    SELECT vcc.tenant_id, vcc.vehicle_id, cd.driver_id, SUM(vcc.images) AS images
    FROM vehicle_cluster_counts vcc
    JOIN cluster_details cd ON cd.cluster_id = vcc.cluster_id
    WHERE cd.driver_id > 0
    GROUP BY vcc.tenant_id, vcc.vehicle_id, cd.driver_id
),
vehicle_totals AS (
    SELECT tenant_id, vehicle_id, SUM(images) AS total_images
    FROM vehicle_driver_counts
    GROUP BY tenant_id, vehicle_id
),
dominant AS (
    SELECT DISTINCT ON (tenant_id, vehicle_id) tenant_id, vehicle_id, driver_id, images
    FROM vehicle_driver_counts
    ORDER BY tenant_id, vehicle_id, images DESC, driver_id ASC
),
vehicle_share AS (
    SELECT d.tenant_id, d.vehicle_id, d.images AS dominant_images, t.total_images,
           d.images::numeric / t.total_images * 100 AS share_pct
    FROM dominant d
    JOIN vehicle_totals t USING (tenant_id, vehicle_id)
),
cutoffs(min_images, min_share_pct) AS (
    VALUES (5, 80.0), (5, 90.0), (10, 80.0), (10, 90.0), (10, 95.0), (20, 90.0), (20, 95.0)
)
SELECT
    c.min_images, c.min_share_pct,
    count(*) FILTER (WHERE vs.dominant_images >= c.min_images AND vs.share_pct >= c.min_share_pct) AS qualifying_vehicles,
    count(*) AS total_vehicles,
    round(100.0 * count(*) FILTER (WHERE vs.dominant_images >= c.min_images AND vs.share_pct >= c.min_share_pct)
          / count(*), 1) AS pct_qualifying
FROM cutoffs c
CROSS JOIN vehicle_share vs
GROUP BY c.min_images, c.min_share_pct
ORDER BY c.min_images, c.min_share_pct;
```

**Output Table:**

| min_images | min_share_pct | qualifying_vehicles | total_vehicles | pct_qualifying |
|---|---|---|---|---|
| 5 | 80.0 | 37,781 | 86,125 | 43.9 |
| 5 | 90.0 | 33,576 | 86,125 | 39.0 |
| 10 | 80.0 | 36,497 | 86,125 | 42.4 |
| 10 | 90.0 | 32,394 | 86,125 | 37.6 |
| 10 | 95.0 | 29,689 | 86,125 | 34.5 |
| 20 | 90.0 | 31,083 | 86,125 | 36.1 |
| 20 | 95.0 | 28,492 | 86,125 | 33.1 |

**Observation:** Total eligible population is 86,125 vehicles — up 37% from the 63,002 seen in
the original run on 2026-11-12, because this count is cumulative across all weeks and more
calendar time has passed. Qualifying *counts* stayed roughly flat (e.g. the 10/90 cutoff:
33,556 → 32,394), so with a larger denominator the qualifying *percentage* dropped substantially
at every cutoff (10/90: 53.3% → 37.6%, a ~29% relative drop). This matches the ticket's own
stated risk — "a vehicle that looks like a single-driver vehicle may have a second driver nobody
has recognized yet" — more history is revealing second/third drivers on vehicles that looked
single-driver on a shorter window.

---

## Step 3: Backtest — Gain Calculation

**Task:** Build the single-driver cohort from the 2026-11-02 history week, exclude fleet-edge
(6.x/8.x firmware) vehicles, then measure the assignment-rate gain if that cohort's unassigned
minutes in the latest complete week (iso_week 45) were auto-assigned — against the whole Wave-1
fleet's denominator, not just the cohort's own.

**DB Used:** IDMS (fleet-edge lookup), FRS (backtest)

**Tables Used:**
- IDMS: `public.ndvehicledeviceconfigurations_copy`, `public.nddevicemaster_copy`
- FRS: `tenant_details_audit`, `tenant_details`, `vehicle_cluster_history`, `cluster_details`,
  `vls_metrics_vehicle_weekly`

**Commands used:**

Fleet-edge fetch (run on IDMS, output saved to `data/fleet_edge_vehicle_ids.csv` — no hardcoded
vehicle_ids; re-run this fetch any time a fresh list is needed):
```sql
\copy (SELECT DISTINCT vdc.vehicle_id FROM public.ndvehicledeviceconfigurations_copy vdc
JOIN public.nddevicemaster_copy dm ON vdc.device_id = dm.manufacturer_device_id_seq
WHERE dm.c_p_version LIKE '6%' OR dm.c_p_version LIKE '8%'
ORDER BY vdc.vehicle_id) TO '/Users/swapnilmane/NetraDyne/driver_history_use/data/fleet_edge_vehicle_ids.csv' WITH CSV HEADER;
```

Verification (same query, run directly without the `\copy` wrapper, to confirm the exported CSV's row count independently):
```sql
SELECT count(DISTINCT vdc.vehicle_id)
FROM public.ndvehicledeviceconfigurations_copy vdc
JOIN public.nddevicemaster_copy dm ON vdc.device_id = dm.manufacturer_device_id_seq
WHERE dm.c_p_version LIKE '6%' OR dm.c_p_version LIKE '8%';
```
Result: **237** — matches the CSV exactly.

Backtest (run on FRS, via `data/step3_backtest.sql`): same structure as Step 2's cluster-to-driver
resolution, plus Wave-1 tenant identification via `tenant_details_audit`, plus the gain
calculation against `vls_metrics_vehicle_weekly`. The `fleet_edge` exclusion list in this file is
auto-generated from the fresh CSV above each time it's regenerated — not hand-typed. Full command
text: see `data/step3_backtest.sql`. Core logic:
```sql
...
candidates AS (
    SELECT d.tenant_id, d.vehicle_id
    FROM dominant d
    JOIN vehicle_totals t USING (tenant_id, vehicle_id)
    WHERE d.images >= 10 AND d.images::numeric / t.total_images >= 0.90
),
fleet AS (
    SELECT sum(m.driving_minutes) AS fleet_driving_minutes, sum(m.vls_assigned_minutes) AS fleet_assigned_minutes
    FROM vls_metrics_vehicle_weekly m
    JOIN wave1_tenants w ON w.tenant_id = m.tenant_id
    WHERE m.iso_year = 2026 AND m.iso_week = 45
      AND m.vehicle_id NOT IN (SELECT vehicle_id FROM fleet_edge)
),
candidate_gain AS (
    SELECT count(*) AS candidate_vehicles, sum(m.total_vls_unassigned_minutes) AS gained_minutes
    FROM candidates c
    JOIN vls_metrics_vehicle_weekly m ON m.tenant_id = c.tenant_id AND m.vehicle_id = c.vehicle_id
        AND m.iso_year = 2026 AND m.iso_week = 45
)
SELECT
    f.fleet_driving_minutes, f.fleet_assigned_minutes, g.candidate_vehicles, g.gained_minutes,
    round(f.fleet_assigned_minutes::numeric / f.fleet_driving_minutes * 100, 3) AS baseline_rate_pct,
    round((f.fleet_assigned_minutes + g.gained_minutes)::numeric / f.fleet_driving_minutes * 100, 3) AS projected_rate_pct,
    round(((f.fleet_assigned_minutes + g.gained_minutes)::numeric - f.fleet_assigned_minutes) / f.fleet_driving_minutes * 100, 3) AS gain_pct_points
FROM fleet f, candidate_gain g;
```

**Output Table:**

| Column | Value |
|---|---|
| fleet_driving_minutes | 63,939,219 |
| fleet_assigned_minutes | 10,721,379 |
| candidate_vehicles | 6,618 |
| gained_minutes | 5,100,064 |
| baseline_rate_pct | 16.768 |
| projected_rate_pct | 24.745 |
| **gain_pct_points** | **7.976** |

**Observation:** Fleet-edge fetch returned 237 vehicle_ids (same count as the original run a week
earlier — no churn in that population). Fleet driving/assigned minutes are noticeably higher than
the original 2026-11-12 run (61.1M → 63.9M driving minutes) even though both target the same
`iso_week = 45` — that week's data was still accumulating during the original run and has since
settled. Candidate count (6,618) is slightly below the original run's 6,709 despite using the
identical history snapshot (`week_start = '2026-11-02'`) and cutoff — a small, second-order drift
worth noting but not alarming. Net result: **gain settles at +7.976pp**, below the original
run's inflated +8.675pp (partial week) and close to the stricter 95%-cutoff re-check's +7.822pp
run separately a few days ago.

---

## Step 4: Precision — Free Contradiction Check

**Task:** For each candidate vehicle (same cohort as Step 3), check its real driving sessions
(DIS) in the target week (iso_week 45) and flag any that went to a driver other than the one
predicted — no labelling tool needed, just comparing the prediction against independently
recorded ground truth.

**DB Used:** FRS (candidate export), IDMS `production-postgresql-driverlogin-ro` /
`beta-prod-idms-db` (contradiction check)

**Tables Used:**
- FRS: `tenant_details_audit`, `tenant_details`, `vehicle_cluster_history`, `cluster_details`
- IDMS: `nddriverinvariant`, `ndfaceimage`

**Commands used:**

Candidate export (FRS, `data/step4_candidates_export.sql`) — same cohort-building logic as
Step 3, final `SELECT` changed to emit `(vehicle_id, candidate_driver_id)` pairs via `\copy`.
Fleet-edge list auto-generated from the fresh CSV, not hand-typed.

Contradiction check (IDMS, 4 batches of ~1,985 candidates each, to stay under the read-replica's
statement timeout — `data/step4_contradiction_check_batch{1..4}.sql`):
```sql
\copy (WITH candidates(vehicle_id, candidate_driver_id) AS (VALUES ...),
dis_windows AS (
    SELECT c.vehicle_id, c.candidate_driver_id, ndi.driver_invariant
    FROM candidates c
    JOIN nddriverinvariant ndi ON ndi.vehicle_id = c.vehicle_id
    WHERE ndi.start_time >= '2026-11-09' AND ndi.start_time < '2026-11-16'
),
latest_face AS (
    SELECT DISTINCT ON (fi.driver_invariant) fi.driver_invariant, fi.driver_id AS vls_driver_id
    FROM ndfaceimage fi
    WHERE fi.driver_invariant IN (SELECT driver_invariant FROM dis_windows)
    ORDER BY fi.driver_invariant, fi.updated_on DESC
)
SELECT
    dw.vehicle_id, dw.candidate_driver_id,
    count(*) AS total_dis,
    count(*) FILTER (WHERE lf.vls_driver_id > 0) AS assigned_dis,
    count(*) FILTER (WHERE lf.vls_driver_id > 0 AND lf.vls_driver_id != dw.candidate_driver_id) AS contradicted_dis
FROM dis_windows dw
LEFT JOIN latest_face lf ON lf.driver_invariant = dw.driver_invariant
GROUP BY dw.vehicle_id, dw.candidate_driver_id
ORDER BY total_dis DESC) TO '...csv' WITH CSV HEADER;
```
(First attempt used an unbounded `start_time >= '2026-11-09'` with no upper limit — caught and
fixed before trusting the result; see Observation.)

**Output Table:**

| Column | Value |
|---|---|
| Candidate vehicles with week-45 DIS data | 7,720 |
| Total DIS | 7,908,991 |
| Assigned DIS | 1,179,390 (14.91%) |
| Contradicted DIS | 317,157 |
| **Contradiction rate (DIS-level)** | **26.89%** |
| Vehicles with ≥1 assigned DIS | 6,116 (79.22%) |
| **Vehicles with ≥1 contradicted DIS** | **51.86%** |

**Observation:** First pass used an unbounded date filter (`start_time >= '2026-11-09'`, no
upper bound) — harmless when run near the start of week 45, but re-running the identical query
days later silently extended the window to ~9 days instead of 7, inflating total_dis to 10.9M
and the vehicle-level contradiction rate to an unreliable 56.59%. Fixed by bounding the window to
`< '2026-11-16'`. Even properly bounded, total_dis (7.9M) is still roughly double the original
2026-11-12 run's 3,999,447 — not a bug this time, but the same partial-week issue already known
from Step 3: the original precision check ran *during* week 45 (only 3-4 days elapsed), so it was
itself computed on a half-finished week, never flagged as such at the time. Today's run, 3 days
after week 45 fully ended, is the first genuinely complete-week precision number. **The corrected
picture is worse than originally reported**: vehicle-level contradiction rate rises from the
originally-reported 41.06% to **51.86%** — with a full week of real data, more candidate vehicles
got the chance to reveal a contradiction. DIS-level rate moved less (25.69% → 26.89%), suggesting
the extra week mostly added volume rather than changing the underlying per-session error rate.

**Combined read of Step 3 + Step 4 — stated precisely, to avoid conflating units:**
- The gain is **+7.976 percentage points** (16.768% → 24.745%) — a pp gain, not a relative "7.9%"
  increase.
- **26.89%** is the DIS-level contradiction rate — out of every individual assigned session, the
  share that disagreed with the prediction. This is the closer answer to "what fraction of
  individual predictions were wrong."
- **51.86%** is a *different* statistic — out of candidate vehicles with any real assignment that
  week, the share showing **at least one** contradicted session. A vehicle with 1 wrong session
  out of 500 still counts fully here; it does not mean most of that vehicle's own predictions
  were wrong. Don't read this as "51.86% of the gain is wrong."
- The two numbers also use different units — gain is measured in **minutes**, precision in **DIS
  (session) counts** — so they cannot be combined into one net "X% of the gain is wrong" figure
  without a unit conversion that hasn't been done rigorously. The honest combined statement is:
  *"the rule would gain +7.976pp, but roughly 1 in 4 individual predictions (26.89%) contradict
  what actually happened, and over half of candidate vehicles (51.86%) show at least one such
  contradiction"* — a real, serious precision concern, stated without conflating the two metrics.

---

## Step 4 (continued): Precision — Method 2, Labelling-Tool Sample

**Status: NOT RUN. Code exists; nothing has been executed.**

**Task:** Get a genuine human-verified precision number, instead of Method 1's proxy (which
checks the rule's prediction against *another automated system's* later decision, not true
ground truth). Sample images the auto-assign rule would actually act on — candidate-vehicle
images with no confident face-recognition match — pair each with a reference photo of the
predicted driver, and push them to NetraDyne's internal Precision Labelling Tool for a human to
mark Match / No-Match / Not-sure.

**DB Used:** FRS (task_type check, image/reference lookup, task registration), S3 (image storage,
not a DB)

**Tables Used:** `labelling_task_type_priority` (safety check), `image_details` +
`arcface_prediction_details` (source of unmatched images), `cluster_details` +
`training_image_details` (source of each candidate driver's reference image, via
`centroid_image_id`), `labelling_task_details` / `labelling_image_details` (write target once
submitted)

**Code:** `scripts/prepare_single_driver_labelling_tasks.py` (already written, subclasses the
same `PrecisionLabellingTaskGenerator` base class AN-36974 uses). `get_images_to_be_labelled()`
finds the unmatched images; `get_candidate_driver_reference_images()` finds each candidate
driver's cluster-centroid reference image; `add_images_for_labelling()` downloads both sides,
builds the side-by-side comparison image, uploads to S3, and registers the task rows.

**Pair composition:** left side = a real, unidentified inward-camera face crop from a candidate
vehicle in the target window (no confident match found); right side = that candidate driver's
cluster-centroid reference face crop (the most representative existing labelled image for that
driver). Same image "shape" on both sides — the labeller's only question is whether they're the
same person.

**Not yet done — required before running:**
1. Confirm `task_type = 5` has no existing row in `labelling_task_type_priority` (read-only
   check; not run yet):
   ```sql
   SELECT * FROM labelling_task_type_priority WHERE task_type = 5;
   ```
2. Decide `--num_tasks` / `--num_relabelling_tasks` sample size.
3. Actually run the script — real side effects (image downloads, S3 uploads, production task
   rows), paused pending explicit review and go-ahead.

**Observation:** Deliberately paused here per request — to be reviewed before deciding whether to
run the script manually.

---

## Step 5: Compare with AN-36281 (using Step 4a data only)

**Task:** Compare this analysis's gain/precision against AN-36281's prior rough estimate, and
give a recommendation — using only Method 1's (free contradiction check) precision data, since
Method 2 (labelling-tool sample) hasn't run yet.

**Output Table:**

| | AN-36281 | This analysis (today's data) |
|---|---|---|
| Gain | +0.8% | +7.976 pp (16.768% → 24.745%) |
| Precision considered | No | Yes — Method 1 only (free check) |
| DIS-level contradiction rate | — | 26.89% |
| Vehicles w/ ≥1 contradiction | — | 51.86% |

**Observation / Recommendation:** Gain is real and far larger than AN-36281's estimate, but
precision isn't safe for hard-assignment — ~1 in 4 individual predictions wrong, over half of
candidate vehicles affected at least once in one week. Suggest a low-confidence/suggested-driver
signal, not silent auto-assign, if this ships. This is based on Method 1 only — not yet backed by
human-verified (Method 2) precision, so treat as preliminary.

---

## Still to do

- Labelling-tool sample (Method 2 of Step 4) — paused, awaiting review/go-ahead to run.
- Step 5 (compare with AN-36281) not yet redone with these refreshed numbers.
