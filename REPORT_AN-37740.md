# AN-37740: Step-by-Step Record

Jira: https://netradyne.atlassian.net/browse/AN-37740

Fresh record, started 2026-11-19. Each step follows the same template as AN-37739's report:
Task, DB Used, Tables Used, Commands used, Output Table, Observation.

---

## Step 1: Week W's -1 Images on Vehicles with History — Count, Sample, Generate Embeddings

**Task:** Per the ticket's Step 1 ("Take week W's images that returned -1 on vehicles with
history"): identify the scope of this population, pull a representative sample per
`tenant_drp` partition, and regenerate embeddings for each sampled image (never persisted -
`arcface_prediction_details` only keeps the final prediction outcome, not the embedding vector).

**DB Used:** FRS

**Tables Used:** `vehicle_cluster_history`, `tenant_details`, `image_details`,
`arcface_prediction_details`

### 1a. Find which `tenant_drp` values are in scope

**Commands used:**
```sql
SELECT DISTINCT td.tenant_drp
FROM vehicle_cluster_history vch
JOIN tenant_details td ON td.tenant_id = vch.tenant_id
WHERE vch.week_start = '2026-11-02'
ORDER BY td.tenant_drp;
```

**Output Table:**

| tenant_drp |
|---|
| 1 |
| 2 |
| 4 |
| 5 |

**Observation:** Same 4 `tenant_drp` values as AN-37739's fleet-edge lookup needed. Required
purely for partition pruning on `image_details`/`arcface_prediction_details` (both partitioned
by `tenant_drp`, then sub-partitioned weekly) - not something the ticket itself asks for.

### 1b. Count -1 images with history, per drp

**Commands used** (per drp, `<DRP_VALUE>` substituted):
```sql
WITH history_vehicles AS (
    SELECT DISTINCT vch.tenant_id, vch.vehicle_id
    FROM vehicle_cluster_history vch
    JOIN tenant_details td ON td.tenant_id = vch.tenant_id
    WHERE vch.week_start = '2026-11-02'
      AND td.tenant_drp = <DRP_VALUE>
      AND (td.config->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean IS TRUE
)
SELECT count(*) AS unassigned_images_with_history
FROM image_details idet
JOIN history_vehicles hv
    ON hv.vehicle_id = idet.vehicle_id AND hv.tenant_id = idet.tenant_id
WHERE idet.tenant_drp = <DRP_VALUE>
  AND idet.timestamp >= '2026-11-09' AND idet.timestamp < '2026-11-16'
  AND NOT EXISTS (
      SELECT 1 FROM arcface_prediction_details apd
      WHERE apd.tenant_drp = <DRP_VALUE>
        AND apd.image_id = idet.image_id
        AND apd.predicted_driver_id > 0
  );
```

**Output Table:**

| tenant_drp | unassigned_images_with_history |
|---|---|
| 1 | 463 |
| 2 | 8,940,658 |
| 4 | 195,597 |
| 5 | *(failed - see Observation; not computed exactly)* |

**Observation:** First version of this query didn't check
`VEHICLE_DRIVER_HISTORY.enabled` at all - only required a row to exist in
`vehicle_cluster_history`. Caught and fixed; re-ran all 3 completed drps with the explicit
enabled-flag filter and got identical counts (verified via a separate mismatch-count query
returning 0) - every tenant with a row also has the flag on right now, so no actual overcounting
had occurred, but the check needed to happen rather than be assumed. drp=5 repeatedly hit
`temporary file size exceeds temp_file_limit` even with `tenant_drp` partition pruning applied -
likely the largest single partition; exact count abandoned as not needed (already know the scale
is multi-million, dominated by drp=2).

### 1c. Sample ~10k images per drp, with embeddings as the eventual goal

**Commands used:** Same CTE structure as above, changed to `SELECT image_id, tenant_id,
vehicle_id, face_s3_path ... LIMIT 10000`, exported via `\copy` to
`data/AN-37740/step_1/an37740_sample_drp<N>.csv`.

For drp=1 and drp=4, `ORDER BY random() LIMIT 10000` worked directly. For drp=2 and drp=5, the
same approach hit `temp_file_limit` (sorting the full multi-million-row match set by a random
key is expensive) - fixed by replacing `ORDER BY random() LIMIT N` with a cheap per-row filter,
`WHERE random() < p` (p chosen to land near 10k expected matches) followed by a plain `LIMIT`
with no sort. drp=5 *still* timed out even with that fix; root cause found: the `NOT EXISTS`
subquery against `arcface_prediction_details` had no date filter of its own, so its own weekly
sub-partitions couldn't be pruned regardless of how narrow `image_details`' side was scoped.
Fixed by adding the same timestamp range to the `apd` side of the subquery too. Even after that,
a single whole-week query for drp=5 kept timing out server-side
(`canceling statement due to statement timeout`) - worked around by splitting drp=5 into 7
daily queries (2,000 rows each) and merging the results locally afterward.

**Output Table:**

| drp | sample size | method |
|---|---|---|
| 1 | 463 (all of it) | `ORDER BY random() LIMIT 10000` |
| 2 | 10,000 | `WHERE random() < 0.0015` + `LIMIT 10000` |
| 4 | 10,000 | `ORDER BY random() LIMIT 10000` |
| 5 | 14,000 | 7 daily queries x 2,000 rows, merged (deduped, no overlaps found) |
| **Total** | **34,463** | |

**Observation:** Chose 10k/drp (not 100k) deliberately - large enough for a meaningful
threshold-sweep sample, small enough to validate the embedding-regeneration pipeline cheaply
before committing to a much bigger compute job. drp=5 needed disproportionate effort to even
sample from (temp_file_limit, then statement_timeout, then day-splitting) - a real asymmetry
between tenant_drp partitions worth remembering if this analysis is ever scaled up.

### 1d. Generate embeddings

**Where this actually ran:** not FRS, not the dev Mac (confirmed the model weights
- `models/arcface/embedding_generator/2/model.pth` and the PNet/RNet/ONet `.npy` files - don't
exist on the Mac or in the git-tracked FaceRecognition checkout) - run on a separate GPU instance
(`/home/ubuntu/FaceRecognition`, `/home/ubuntu/driver_history_use`) where those weights are
present on disk.

**Script:** `scripts/an37740_generate_embeddings.py` - uses `frs_src`'s own
`ThreadedDownloader` (parallel S3 download) and `GenerateArcfaceEmbeddings.process_images()`
unchanged; no reimplementation of detection/alignment/embedding logic. Output is a local pickle
file only - no DB writes, no uploads.

**Commands used:**
```bash
python scripts/an37740_generate_embeddings.py --csv data/AN-37740/step_1/an37740_sample_drp<N>.csv --out data/AN-37740/step_1/an37740_embeddings_drp<N>.pkl
```

**Output Table:**

| drp | sample size | embedded (face detected) | detection failed | detection rate |
|---|---|---|---|---|
| 1 | 463 | 245 | 218 | 52.9% |
| 2 | 10,000 | 7,653 | 2,347 | 76.5% |
| 4 | 10,000 | 7,888 | 2,112 | 78.9% |
| 5 | 14,000 | 12,652 | 1,348 | 90.4% |
| **Total** | **34,463** | **28,438** | **6,025** | **82.5%** |

**Observation:** Detection rate (the embedding pipeline's own face-detection stage, before any
ArcFace comparison even happens) varies a lot by tenant_drp - 52.9% (drp1) to 90.4% (drp5), a
~37-point spread. This matters for the ticket: a meaningful chunk of the -1 pool fails at face
detection, not at the matching-threshold stage - no amount of lowering AN-37740's proposed
threshold can ever recover those, since they're a hard ceiling on coverage independent of
threshold choice. drp2 (the single largest volume contributor to the overall -1-with-history
population, ~8.9M of ~9.1M+ counted) sits mid-range at 76.5% - closer to the fleet-wide picture
than drp1's low outlier or drp5's high outlier.

Download step was originally sequential (`FileUtils.download_images()`) - 825s for the drp5
14k-image run alone. Switched to `ThreadedDownloader` (20 parallel threads, same underlying S3
download call, just parallelized) - cut drp2's equivalent download step from an extrapolated
~590s down to ~63s.

---

## Still to do

- Step 2 (compare each embedded image against its vehicle's candidate drivers' reference
  embeddings, from `training_image_details.embedding` - already stored, no regeneration needed
  there) - not started.
- Threshold sweep, gain/precision per threshold, recommended threshold - not started.
- Whether algos 1/2 (`frs_src/utils.py`'s `post_process`) can be reused - already answered:
  no, not directly (they only reject positive predictions, never rescue a -1); only their
  `driver_vehicle_mapping` lookup structure is reusable.
