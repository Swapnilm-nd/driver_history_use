# AN-37740: Step-by-Step Record

Jira: https://netradyne.atlassian.net/browse/AN-37740

Fresh restart, 2026-11-19. Scope for this pass: **tenant_drp = 1 only**. Same template as
before: Task, DB Used, Tables Used, Commands used, Output Table, Observation.

---

## Step 1: Week W's Unassigned Images on Vehicles with History (drp=1)

**Task:** Per the ticket's Step 1 ("Take week W's images that returned -1 on vehicles with
history"): count the -1/no-prediction images in week W, on vehicles that have history, scoped
to tenant_drp=1.

**Week W = week 45, 2026 (2026-11-09 to 2026-11-15)** - the latest complete week. History week
= week 44, 2026 (2026-11-02 to 2026-11-08) - the only complete week of lookback available.

**DB Used:** FRS

**Tables Used:** `vehicle_cluster_history`, `tenant_details`, `image_details`,
`arcface_prediction_details`

**Commands used:**
```sql
WITH history_vehicles AS (
    SELECT DISTINCT vch.tenant_id, vch.vehicle_id
    FROM vehicle_cluster_history vch
    JOIN tenant_details td ON td.tenant_id = vch.tenant_id
    WHERE vch.week_start = '2026-11-02'
      AND td.tenant_drp = 1
      AND (td.config->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean IS TRUE
)
SELECT count(*) AS unassigned_images_with_history
FROM image_details idet
JOIN history_vehicles hv
    ON hv.vehicle_id = idet.vehicle_id AND hv.tenant_id = idet.tenant_id
WHERE idet.tenant_drp = 1
  AND idet.timestamp >= '2026-11-09' AND idet.timestamp < '2026-11-16'
  AND NOT EXISTS (
      SELECT 1 FROM arcface_prediction_details apd
      WHERE apd.tenant_drp = 1
        AND apd.timestamp >= '2026-11-09' AND apd.timestamp < '2026-11-16'
        AND apd.image_id = idet.image_id
        AND apd.predicted_driver_id > 0
  );
```

**Output Table:**

| unassigned_images_with_history |
|---|
| 463 |

**Observation:** Matches the count found in the earlier (now-deleted) pass - confirms the
underlying data hasn't shifted and we're starting this fresh attempt from the same known-good
baseline. `tenant_drp = 1` filters applied on both `image_details` and
`arcface_prediction_details` (not just one side) for partition pruning - both tables are
sub-partitioned weekly within each `tenant_drp`, so a date filter on only one side leaves the
other unpruned.

---

## Step 1 (continued): Fetch Rows + Generate Embeddings (drp=1)

**Task:** Pull all 463 rows (`image_id, tenant_id, vehicle_id, face_s3_path`), move them to the
instance with the actual ArcFace model weights, and regenerate embeddings - never persisted
anywhere, since `arcface_prediction_details` only keeps the final -1 outcome, not the vector.

**DB Used:** FRS (fetch). **Run location (embeddings):** separate GPU instance
(`/home/ubuntu/FaceRecognition`, `/home/ubuntu/driver_history_use`) - confirmed earlier the
model weights don't exist on the dev Mac or in the git-tracked FaceRecognition checkout.

**Tables Used:** `image_details`, `arcface_prediction_details`, `vehicle_cluster_history`,
`tenant_details`

**Commands used:**

Fetch (FRS, `data/AN-37740/step_1/an37740_step1_fetch_drp1.sql`) - same query as the count
above, with `SELECT image_id, tenant_id, vehicle_id, face_s3_path` instead of `count(*)`, no
sampling needed since 463 is already small.

Embeddings (instance, `scripts/an37740_generate_embeddings.py` - uses `frs_src`'s own
`ThreadedDownloader` + `GenerateArcfaceEmbeddings.process_images()` unchanged):
```bash
python scripts/an37740_generate_embeddings.py --csv data/AN-37740/step_1/an37740_images_drp1.csv --out data/AN-37740/step_1/an37740_embeddings_drp1.pkl
```

**Output Table:**

| | Count |
|---|---|
| Total images | 463 |
| Embedded (face detected) | 245 |
| Detection failed | 218 |
| Detection rate | 52.9% |

**Observation:** Identical to the pre-restart run - confirms this is a stable, reproducible
result, not an artifact of the earlier (now-deleted) attempt.

---

## Step 2: Candidate Drivers Check (drp=1) - Dead End

**Task:** Find candidate drivers (week 44 history) for drp=1's 463-image population.

**DB Used:** FRS. **Tables Used:** `vehicle_cluster_history`, `cluster_details`

**Commands used:**
```sql
-- only 2 distinct vehicles in the drp1 sample: (5730, 1092496), (5730, 2750039)
SELECT vch.vehicle_id, kv.key::bigint AS cluster_id, kv.value::int AS images, cd.driver_id
FROM vehicle_cluster_history vch
CROSS JOIN LATERAL jsonb_each_text(vch.cluster_counts) AS kv(key, value)
LEFT JOIN cluster_details cd ON cd.cluster_id = kv.key::bigint
WHERE vch.week_start = '2026-11-02'
  AND (vch.tenant_id, vch.vehicle_id) IN ((5730, 1092496), (5730, 2750039));
```

**Output Table:**

| vehicle_id | cluster_id | images | driver_id |
|---|---|---|---|
| 2750039 | 1979608 | 130 | -1 |
| 1092496 | 2417634 | 1 | -1 |
| 1092496 | 2573165 | 1 | -1 |

**Observation:** drp=1's entire -1-image population (463 images) traces back to only 2
vehicles, and both vehicles' complete week-44 history is unlabelled (`driver_id = -1`) - zero
labelled candidate drivers exist for either one. **0 of drp=1's 245 embedded images have any
candidate to compare against** - not a threshold problem, a complete absence of labelled
history for this partition. Set aside; moving to drp=2 to actually exercise the comparison
logic.

---

## Step 1 (drp=2): History Vehicle Count

**Task:** Same as drp=1's Step 1, scoped to tenant_drp=2 - check scale before fetching.

**DB Used:** FRS. **Tables Used:** `vehicle_cluster_history`, `tenant_details`

**Commands used:**
```sql
WITH history_vehicles AS (
    SELECT DISTINCT vch.tenant_id, vch.vehicle_id
    FROM vehicle_cluster_history vch
    JOIN tenant_details td ON td.tenant_id = vch.tenant_id
    WHERE vch.week_start = '2026-11-02'
      AND td.tenant_drp = 2
      AND (td.config->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean IS TRUE
)
SELECT count(*) AS history_vehicle_count FROM history_vehicles;
```

**Output Table:**

| history_vehicle_count |
|---|
| 38,674 |

**Observation:** drp=2 has 38,674 vehicles with history - vastly more than drp=1's 2. Next:
the -1-image count for drp=2 (expect ~8,940,658, matching the pre-restart run).

---

## Step 1 (drp=2): Filter to Vehicles with at Least One Labelled Candidate

**Task:** Same lesson as drp=1's dead end - filter the 38,674 history vehicles down to only
ones with ≥1 labelled candidate driver, before spending any effort fetching -1 images or
embeddings for vehicles that could never be compared against anything.

**DB Used:** FRS. **Tables Used:** `vehicle_cluster_history`, `tenant_details`,
`cluster_details`

**Commands used:**
```sql
WITH history_vehicles AS (
    SELECT DISTINCT vch.tenant_id, vch.vehicle_id
    FROM vehicle_cluster_history vch
    JOIN tenant_details td ON td.tenant_id = vch.tenant_id
    WHERE vch.week_start = '2026-11-02'
      AND td.tenant_drp = 2
      AND (td.config->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean IS TRUE
),
vehicle_cluster_counts AS (
    SELECT vch.tenant_id, vch.vehicle_id, kv.key::bigint AS cluster_id
    FROM vehicle_cluster_history vch
    JOIN history_vehicles hv ON hv.tenant_id = vch.tenant_id AND hv.vehicle_id = vch.vehicle_id
    CROSS JOIN LATERAL jsonb_each_text(vch.cluster_counts) AS kv(key, value)
    WHERE vch.week_start = '2026-11-02'
)
SELECT count(DISTINCT (vcc.tenant_id, vcc.vehicle_id)) AS vehicles_with_labelled_candidate
FROM vehicle_cluster_counts vcc
JOIN cluster_details cd ON cd.cluster_id = vcc.cluster_id
WHERE cd.driver_id > 0;
```

**Output Table:**

| | Count | % of 38,674 |
|---|---|---|
| Vehicles with ≥1 labelled candidate driver | 26,371 | 68.2% |
| Dead-end vehicles (history exists, but entirely unlabelled) | 12,303 | 31.8% |

**Observation:** Nearly a third of drp=2's history vehicles are dead ends, same issue as
drp=1's 2 vehicles - their entire week-44 history is unlabelled clusters, no candidate driver
to compare against regardless of threshold. Scoping the rest of this pass (the -1-image fetch,
embeddings, comparison) to only the 26,371 vehicles that actually have a usable candidate -
export query: `data/AN-37740/step_1/an37740_drp2_vehicles_with_candidate_export.sql`.

---

## Step 1 (drp=2): Scope Narrowed to 200 Vehicles, Fetch Rows

**Task:** 26,371 "has-candidate" vehicles was too much to process for drp=2 in one pass (would
have been 8,940,658 images at the full history-vehicle scope). Narrowed step by step: 500
vehicles -> 70,791 images (still large), 200 vehicles -> 46,912 images - accepted as the
working scope for this pass.

**DB Used:** FRS. **Tables Used:** `vehicle_cluster_history`, `tenant_details`,
`cluster_details`, `image_details`, `arcface_prediction_details`

**Commands used:** same `vehicles_with_candidate` CTE as before, with `LIMIT 200` added, then
counted and fetched for just those 200 vehicles
(`data/AN-37740/step_1/an37740_step1_fetch_drp2_200vehicles.sql`). Note: `LIMIT` here picks
*some* 200 vehicles (whatever the query planner returns first), not a random sample.

**Output Table:**

| Vehicles scoped | unassigned_images |
|---|---|
| 500 | 70,791 |
| **200 (accepted)** | **46,912** |

**Observation:** Confirms the fetch count matches the count query exactly (46,912 both times).
Next: move this CSV to the instance and generate embeddings, same as drp=1.

---

## Still to do

- Unassigned-image count for drp=2 (week W=45), then fetch + embeddings.
- Candidate drivers per vehicle for drp=2 (week 44 history) - pulling **every cluster** per
  candidate driver this time, not just one representative cluster per driver (the gap caught
  before restarting: the ticket says "candidates' cluster embeddings," plural).
- Comparison, threshold sweep, gain/precision per threshold, recommended threshold.
