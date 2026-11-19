"""
AN-37740: generate ArcFace embeddings for the sampled -1/no-prediction images.

These images' embeddings were never persisted anywhere (arcface_prediction_details only
keeps the final prediction outcome, not the vector) - confirmed by reading frs_src/orm.py.
This script regenerates them locally, using frs_src's own GenerateArcfaceEmbeddings class and
ThreadedDownloader unchanged (no reimplementation of detection/alignment/embedding/download
logic), so it only runs where the actual model weights exist - this machine
(/home/ubuntu/FaceRecognition), not the personal Mac this project was otherwise developed on
(confirmed missing there). Downloads run in parallel (--num_threads, default 20) - that step
was the dominant cost on a 14k-image run (825s of it, sequential).

Input: a sample CSV with columns image_id,tenant_id,vehicle_id,face_s3_path (e.g.
driver_history_use/data/AN-37740/step_1/an37740_sample_drp1.csv).

Output: a local pickle file - list of dicts {image_id, tenant_id, vehicle_id, face_s3_path,
embedding (list[float] or None if face detection failed)}. No DB writes, no uploads - purely
local output for this analysis.

Run (on the instance with the model weights):
    python scripts/an37740_generate_embeddings.py \
        --csv data/AN-37740/step_1/an37740_sample_drp1.csv \
        --out data/AN-37740/step_1/an37740_embeddings_drp1.pkl
"""
import os
import sys
import csv
import pickle
import logging
import tempfile

FACE_RECOGNITION_REPO_ROOT = "/home/ubuntu/FaceRecognition"
if FACE_RECOGNITION_REPO_ROOT not in sys.path:
    sys.path.insert(0, FACE_RECOGNITION_REPO_ROOT)

from frs_src.utils import ThreadedDownloader
from frs_src.models import GenerateArcfaceEmbeddings
from frs_src.logging_init import initialize_logging

logger = logging.getLogger("frs_src")


def load_sample(csv_path):
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def main(csv_path, out_path, device, num_threads):
    # frs_src/arcface_utils/get_nets.py loads its weights via a hardcoded relative path
    # ("./models/arcface/mtcnn/pnet.npy"), so the process's cwd must be the FaceRecognition
    # repo root for that to resolve. Resolve our own paths to absolute first, since they're
    # relative to wherever this script was invoked from (driver_history_use), not to
    # FaceRecognition - otherwise the chdir below would silently break them.
    csv_path = os.path.abspath(csv_path)
    out_path = os.path.abspath(out_path)
    os.chdir(FACE_RECOGNITION_REPO_ROOT)

    rows = load_sample(csv_path)
    logger.info("loaded %s sample rows from %s", len(rows), csv_path)

    face_s3_paths = [row["face_s3_path"] for row in rows]

    # FileUtils.download_images() downloads one image at a time - the dominant cost on a
    # 14k-image run (825s of it). ThreadedDownloader does the same S3 download in parallel
    # (num_threads workers); it doesn't return a {s3_path: local_path} mapping itself, so we
    # rebuild it the same way it names files internally (basename of the S3 key).
    download_dir = tempfile.mkdtemp(prefix="an37740_images_")
    ThreadedDownloader().download_objects(face_s3_paths, download_dir, num_threads=num_threads)
    s3_path_mapping = {}
    for path in face_s3_paths:
        local_path = os.path.join(download_dir, path[path.rfind("/") + 1 :])
        if os.path.exists(local_path):
            s3_path_mapping[path] = local_path
    logger.info(
        "downloaded %s/%s images (%s threads)",
        len(s3_path_mapping),
        len(face_s3_paths),
        num_threads,
    )

    generator = GenerateArcfaceEmbeddings(device)
    results = generator.process_images(s3_path_mapping)
    logger.info("ran detection+embedding on %s images", len(results))

    # results is keyed by face_s3_path too (process_images preserves it per entry) - merge
    # back onto the original rows to carry image_id/tenant_id/vehicle_id through.
    results_by_s3_path = {r["face_s3_path"]: r for r in results}

    output = []
    n_embedded = 0
    for row in rows:
        result = results_by_s3_path.get(row["face_s3_path"])
        embedding = result["embedding"] if result else None
        if embedding is not None:
            n_embedded += 1
        output.append(
            {
                "image_id": row["image_id"],
                "tenant_id": row["tenant_id"],
                "vehicle_id": row["vehicle_id"],
                "face_s3_path": row["face_s3_path"],
                "embedding": embedding,
            }
        )

    with open(out_path, "wb") as f:
        pickle.dump(output, f)
    logger.info(
        "wrote %s rows to %s (%s with a real embedding, %s where face detection failed)",
        len(output),
        out_path,
        n_embedded,
        len(output) - n_embedded,
    )

    # cleanup downloaded temp images - not needed once embeddings are extracted
    import shutil

    shutil.rmtree(download_dir, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    initialize_logging(logger, True, "offline", "an37740-generate-embeddings", logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True, help="sample CSV with face_s3_path column")
    parser.add_argument("--out", required=True, help="output pickle path")
    parser.add_argument(
        "--device", type=int, default=0, help="CUDA device index if a GPU is available, else CPU"
    )
    parser.add_argument(
        "--num_threads", type=int, default=20, help="parallel S3 download threads"
    )
    args = parser.parse_args()
    main(args.csv, args.out, args.device, args.num_threads)
