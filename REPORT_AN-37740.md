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

## Still to do

- Sample/pull these 463 images and regenerate their embeddings (same approach as before -
  `scripts/an37740_generate_embeddings.py`, runs on the instance with the actual model weights).
- Candidate drivers per vehicle (week 44 history) - this time pulling **every cluster** per
  candidate driver, not just one representative cluster per driver (the gap caught before
  restarting: the ticket says "candidates' cluster embeddings," plural, so a multi-cluster
  driver needs comparison against all of their clusters, not just one).
- Comparison, threshold sweep, gain/precision per threshold, recommended threshold.
