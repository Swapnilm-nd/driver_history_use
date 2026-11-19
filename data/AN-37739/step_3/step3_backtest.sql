-- AN-37739 Step 3 backtest. fleet_edge VALUES below are auto-generated (not hand-typed)
-- from this session's fresh fetch in data/fleet_edge_vehicle_ids.csv (237 rows) --
-- re-run the fetch query + this generation step together whenever a truly fresh list is needed.
WITH tenant_audit AS (
    SELECT tenant_id, changed_at,
        (old_data->'config'->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean AS vdh_enabled_before
    FROM tenant_details_audit
),
enabling_rows AS (
    SELECT tenant_id, MAX(changed_at) AS vdh_enabled_at
    FROM tenant_audit WHERE vdh_enabled_before IS NOT TRUE GROUP BY tenant_id
),
wave1_tenants AS (
    SELECT er.tenant_id FROM enabling_rows er
    JOIN tenant_details td ON td.tenant_id = er.tenant_id
    WHERE er.vdh_enabled_at < '2026-11-02'
      AND (td.config->'VEHICLE_DRIVER_HISTORY'->>'enabled')::boolean IS TRUE
),
fleet_edge(vehicle_id) AS (
    VALUES (24704), (76513), (81017), (81062), (125383), (125384), (199128), (289897), (291620), (300381), (302896), (309819), (315947), (317625), (322941), (357141), (357142), (366130), (369926), (398058), (399521), (414915), (467137), (479769), (510079), (517436), (522693), (532704), (536991), (555245), (578013), (604390), (604391), (604392), (635505), (635506), (635507), (635509), (635510), (635511), (635512), (635513), (635514), (635515), (635516), (635517), (635518), (635519), (635520), (635521), (635522), (635523), (635524), (635525), (635526), (635527), (635528), (635530), (635531), (635533), (635534), (635535), (635537), (635538), (635539), (635540), (635541), (635542), (635543), (635544), (635545), (635547), (635548), (635550), (635551), (635552), (635553), (635554), (635555), (635556), (635557), (635558), (635559), (635560), (635561), (635562), (635563), (635564), (635565), (635567), (635568), (635569), (635570), (635583), (635585), (635588), (635595), (635598), (635599), (635607), (635622), (635623), (635624), (635625), (635626), (635627), (635628), (635629), (635630), (635631), (635632), (635633), (635634), (635636), (635637), (635638), (635639), (635640), (635641), (635642), (635643), (635644), (635645), (635646), (635647), (635648), (635649), (635650), (635651), (635653), (635654), (635655), (635656), (635657), (635658), (635659), (635660), (635661), (635662), (635663), (635664), (635665), (635666), (635667), (635668), (635669), (635670), (635671), (635672), (635673), (635674), (635675), (635676), (635677), (635678), (635679), (635680), (635681), (635682), (635683), (635684), (635685), (635686), (635687), (635688), (635689), (635690), (635691), (635692), (635695), (635696), (635699), (635700), (635703), (635704), (635705), (635706), (635707), (635708), (635709), (635710), (635711), (635712), (635713), (635714), (635716), (635717), (635718), (635719), (635720), (635721), (635722), (635723), (635724), (635725), (635726), (635727), (635728), (635729), (635730), (635731), (635732), (635733), (635734), (635735), (635736), (635737), (635738), (635739), (635740), (635741), (635742), (635743), (635744), (635746), (635848), (635857), (635858), (635967), (635971), (635973), (635987), (635994), (635997), (636007), (638064), (638067), (638078), (638079), (638081), (638082), (638083), (638084), (643906), (655171), (655185), (661108)
),
vehicle_cluster_counts AS (
    SELECT vch.tenant_id, vch.vehicle_id, kv.key::bigint AS cluster_id, kv.value::int AS images
    FROM vehicle_cluster_history vch
    JOIN wave1_tenants w ON w.tenant_id = vch.tenant_id
    CROSS JOIN LATERAL jsonb_each_text(vch.cluster_counts) AS kv(key, value)
    WHERE vch.week_start = '2026-11-02'
      AND vch.vehicle_id NOT IN (SELECT vehicle_id FROM fleet_edge)
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
    FROM vehicle_driver_counts GROUP BY tenant_id, vehicle_id
),
dominant AS (
    SELECT DISTINCT ON (tenant_id, vehicle_id) tenant_id, vehicle_id, driver_id, images
    FROM vehicle_driver_counts
    ORDER BY tenant_id, vehicle_id, images DESC, driver_id ASC
),
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
