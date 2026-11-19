"""
AN-37740 Step 2: compare each sampled -1 image's embedding against its vehicle's candidate
drivers' reference embeddings, keep the best match + its similarity score.

Pure numpy over already-computed data (no model weights, no frs_src needed) - unlike the
embedding-generation script, this can run anywhere the three inputs below are present.

Inputs:
  --embeddings_pkl  one or more pickle files from an37740_generate_embeddings.py
                     (image_id, tenant_id, vehicle_id, face_s3_path, embedding)
  --candidates_csv  (tenant_id, vehicle_id, driver_id) - which drivers are candidates for
                     which vehicle
  --reference_csv   (driver_id, cluster_id, embedding) - ONE ROW PER CLUSTER, not per driver.
                     A driver with multiple clusters has multiple rows here - per the ticket's
                     "candidates' cluster embeddings" (plural), every cluster of a candidate
                     driver is compared, not just one representative embedding per driver.
                     embedding is a Postgres array literal, e.g. "{0.1,0.2,...}"

Output: a CSV - one row per image that had a real embedding (detection didn't fail) AND had
at least one candidate driver for its vehicle: image_id, tenant_id, vehicle_id,
num_candidate_drivers, num_clusters_compared, best_driver_id, best_cluster_id,
best_similarity (cosine, the best across every cluster of every candidate).

Similarity metric: cosine similarity, matching cfg.ARCFACE.cosine_similarity_threshold (0.53)
in frs_src/config.py - "the current value" the ticket's threshold sweep starts from.

Run:
    python scripts/an37740_compare_embeddings.py \
        --embeddings_pkl data/AN-37740/step_1/an37740_embeddings_drp2_200vehicles.pkl \
        --candidates_csv data/AN-37740/step_1/an37740_drp2_200v_candidate_drivers.csv \
        --reference_csv db_embeddings/an37740_drp2_200v_reference_embeddings.csv \
        --out data/AN-37740/step_2/an37740_best_match_drp2_200v.csv
"""
import os
import csv
import pickle
import logging

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def parse_pg_array(s):
    """Postgres array literal, e.g. '{0.1,0.2,-0.3}' -> np.array([0.1, 0.2, -0.3])."""
    s = s.strip().strip("{}")
    if not s:
        return None
    return np.array([float(x) for x in s.split(",")], dtype=np.float32)


def load_embeddings(pkl_paths):
    rows = []
    for path in pkl_paths:
        with open(path, "rb") as f:
            rows.extend(pickle.load(f))
    logger.info("loaded %s image rows from %s pickle file(s)", len(rows), len(pkl_paths))
    return rows


def load_candidates(csv_path):
    """{(tenant_id, vehicle_id): [driver_id, ...]}"""
    candidates = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            key = (row["tenant_id"], row["vehicle_id"])
            candidates.setdefault(key, []).append(row["driver_id"])
    logger.info("loaded candidate drivers for %s vehicles", len(candidates))
    return candidates


def load_reference_embeddings(csv_path):
    """{driver_id: [(cluster_id, np.array), ...]} - a driver with multiple clusters gets
    multiple entries, since the ticket compares against every cluster, not one per driver."""
    references = {}
    n_clusters = 0
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            emb = parse_pg_array(row["embedding"])
            if emb is not None:
                references.setdefault(row["driver_id"], []).append((row["cluster_id"], emb))
                n_clusters += 1
    logger.info(
        "loaded %s reference cluster embeddings for %s distinct drivers",
        n_clusters,
        len(references),
    )
    return references


def cosine_similarity(a, b):
    denom = np.linalg.norm(a) * np.linalg.norm(b)
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)


def main(embeddings_pkl, candidates_csv, reference_csv, out_path):
    image_rows = load_embeddings(embeddings_pkl)
    candidates = load_candidates(candidates_csv)
    references = load_reference_embeddings(reference_csv)

    output = []
    n_no_embedding = 0
    n_no_candidates = 0
    n_no_reference = 0

    for row in image_rows:
        if row["embedding"] is None:
            n_no_embedding += 1
            continue

        key = (row["tenant_id"], row["vehicle_id"])
        driver_ids = candidates.get(key)
        if not driver_ids:
            n_no_candidates += 1
            continue

        image_emb = np.array(row["embedding"], dtype=np.float32)
        best_driver_id = None
        best_cluster_id = None
        best_similarity = None
        n_candidate_drivers_with_reference = 0
        n_clusters_compared = 0
        for driver_id in driver_ids:
            driver_clusters = references.get(driver_id)
            if not driver_clusters:
                continue
            n_candidate_drivers_with_reference += 1
            for cluster_id, ref_emb in driver_clusters:
                n_clusters_compared += 1
                similarity = cosine_similarity(image_emb, ref_emb)
                if best_similarity is None or similarity > best_similarity:
                    best_similarity = similarity
                    best_driver_id = driver_id
                    best_cluster_id = cluster_id

        if n_clusters_compared == 0:
            n_no_reference += 1
            continue

        output.append(
            {
                "image_id": row["image_id"],
                "tenant_id": row["tenant_id"],
                "vehicle_id": row["vehicle_id"],
                "num_candidate_drivers": n_candidate_drivers_with_reference,
                "num_clusters_compared": n_clusters_compared,
                "best_driver_id": best_driver_id,
                "best_cluster_id": best_cluster_id,
                "best_similarity": round(best_similarity, 6),
            }
        )

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "image_id",
                "tenant_id",
                "vehicle_id",
                "num_candidate_drivers",
                "num_clusters_compared",
                "best_driver_id",
                "best_cluster_id",
                "best_similarity",
            ],
        )
        writer.writeheader()
        writer.writerows(output)

    logger.info(
        "wrote %s rows to %s (skipped: %s no embedding, %s no candidates for vehicle, "
        "%s candidates had no reference embedding)",
        len(output),
        out_path,
        n_no_embedding,
        n_no_candidates,
        n_no_reference,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--embeddings_pkl", nargs="+", required=True)
    parser.add_argument("--candidates_csv", required=True)
    parser.add_argument("--reference_csv", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    main(args.embeddings_pkl, args.candidates_csv, args.reference_csv, args.out)
