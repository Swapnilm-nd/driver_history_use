"""
AN-37739: creates labelling tasks to precision-check the single-driver-vehicle auto-assign rule.

Inherits PrecisionLabellingTaskGenerator as-is and overrides what has to differ - same pattern
prepare_ambiguous_labelling_tasks.py (AN-36974) already uses to extend this same base class:

  - Candidates (vehicle_id, candidate_driver_id) come from an external CSV - the output of the
    AN-37739 analysis in this project, not computed here. This script doesn't decide which
    vehicles qualify as single-driver; it only samples images for whichever vehicles it's given.
  - get_images_to_be_labelled(): source is images on candidate vehicles with NO usable
    prediction (predicted_driver_id is null or <=0, or no prediction row at all) in the target
    window - the exact pool the auto-assign rule would act on - instead of "image has >=1
    matched cluster" (the parent class) or "predicted_driver_id = -2" (AN-36974's variant).
  - The reference/comparison image is the CANDIDATE DRIVER's own representative cluster, looked
    up by driver_id via get_candidate_driver_reference_images() - not one of the image's own
    matched clusters, since these images have no match at all to pick from.

create_comparison_image(), register_tasks_to_database(), relabel_sampled_images() are the
parent class's own code, inherited unchanged. A labeller's existing Match/No-Match/Not-sure
vocabulary applies unchanged too: Match = the rule's assignment would have been correct,
No-Match = it would have been wrong - exactly Step 4's precision question, no new label
semantics needed.

Partition note: image_details/arcface_prediction_details are partitioned by tenant_drp (then
sub-partitioned weekly). Production code loops one tenant_drp at a time to get partition
pruning (see frs_src/assignment_rate_tool/app.py's _prediction_join_clause). This script
deliberately does NOT - the AN-37739 candidate list spans many different tenant_drps across
801 tenants, so there's no single tenant_drp to scope to; the vehicle_id + timestamp-window
filters still prune each partition's own index, just across all of them rather than one.
Acceptable for a one-off, small (thousands of images) offline sampling task; would need
revisiting if this script were ever run at a much larger scale.

labelling_task_type_priority must NOT contain a row for whichever task_type this is run with:
frs_src/image_labelling serves every task_type listed there, so adding one here would let it
serve these before this script is reviewed as ready (same constraint AN-36974 documents).

NOT RUN YET - see REPORT.md for status. Candidates CSV expected at the path produced by this
project's Step 4 work (e.g. data/step4_candidates_wave1.csv), with a fleet-edge-excluded
vehicle_id list already applied upstream if desired.

This script lives in driver_history_use/ (the analysis project) but depends on frs_src (already
pip-installed, see README.md) and on prepare_precision_labelling_tasks.py, a top-level script in
the FaceRecognition repo itself (not part of the installed package) - the sys.path insert below
makes that importable from here.

Run from anywhere (once candidates are finalized and this is reviewed):
    python scripts/prepare_single_driver_labelling_tasks.py \
        --candidates_csv data/step4_candidates_wave1.csv --num_tasks 50 \
        --num_relabelling_tasks 10 [--task_type 5]
"""
import os
import sys
import math
import logging

import pandas as pd
import sqlalchemy as sa

# The FaceRecognition repo root - where prepare_precision_labelling_tasks.py (a top-level
# script, not part of the pip-installed frs_src package) actually lives. Adjust if that
# worktree moves.
FACE_RECOGNITION_REPO_ROOT = (
    "/Users/swapnilmane/NetraDyne/FaceRecognition/.claude/worktrees/"
    "system-design-implementation-62aaf6"
)
if FACE_RECOGNITION_REPO_ROOT not in sys.path:
    sys.path.insert(0, FACE_RECOGNITION_REPO_ROOT)

import frs_src.orm as orm
from frs_src.config import cfg
from frs_src.logging_init import initialize_logging
from frs_src.utils import FileUtils
from prepare_precision_labelling_tasks import PrecisionLabellingTaskGenerator

logger = logging.getLogger("frs_src")

TASK_TYPE = 5


class SingleDriverLabellingTaskGenerator(PrecisionLabellingTaskGenerator):
    def __init__(self, candidates_df, *args, task_type=TASK_TYPE, **kwargs):
        # tenant_drp=None: candidates span many tenant_drps, there's no single one to pass -
        # same None the reference scripts themselves use for their own relabel_sampled_images()
        # pass, since that method doesn't filter by tenant_drp either.
        super().__init__(None, *args, **kwargs)
        self.candidates_df = candidates_df
        self.task_type = task_type
        self.upload_folder = "single_driver_estimation"
        self.download_folder = "/tmp/single_driver_labelling_images"
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)

    def get_images_to_be_labelled(self):
        """Images on candidate vehicles, in the target window, with no usable prediction - the
        exact pool the auto-assign rule would act on. candidate_driver_id is carried through
        from the input CSV (one row per candidate vehicle) via the final merge, not looked up
        here - this query only needs vehicle_id to find the right images."""
        vehicle_ids = self.candidates_df["vehicle_id"].tolist()
        query = (
            self.session.query(
                orm.ImageDetails.tenant_id,
                orm.ImageDetails.vehicle_id,
                orm.ImageDetails.image_id,
                orm.ImageDetails.timestamp.label("image_arrival_timestamp"),
                orm.ImageDetails.face_s3_path.label("inference_image_s3_path"),
            )
            .outerjoin(
                orm.ArcfacePredictionDetails,
                orm.ArcfacePredictionDetails.image_id == orm.ImageDetails.image_id,
            )
            .filter(orm.ImageDetails.vehicle_id.in_(vehicle_ids))
            .filter(
                sa.or_(
                    orm.ArcfacePredictionDetails.image_id.is_(None),
                    orm.ArcfacePredictionDetails.predicted_driver_id.is_(None),
                    orm.ArcfacePredictionDetails.predicted_driver_id <= 0,
                )
            )
            .filter(
                orm.ImageDetails.timestamp >= self.start_date,
                orm.ImageDetails.timestamp < self.end_date,
            )
            .order_by(sa.func.random())
            .limit(self.num_images_to_label)
        )
        image_df = self.db_utils.run_query_to_df(query)
        if image_df.empty:
            return image_df
        return image_df.merge(self.candidates_df, on="vehicle_id", how="inner")

    def get_candidate_driver_reference_images(self, driver_ids):
        """One representative training image per candidate driver_id - the comparison target,
        in place of get_matched_cluster_details() (nothing is matched here, so there's no
        per-image cluster to pick from). Picks a single deterministic cluster per driver
        (lowest cluster_id) rather than their largest cluster - ranking by image count would
        need an extra join this script doesn't otherwise require; any one of a driver's
        clusters is a legitimate reference face for the labeller's Match/No-Match judgment."""
        query = (
            self.session.query(
                orm.ClusterDetails.driver_id,
                orm.ClusterDetails.cluster_id.label("matched_cluster_id"),
                orm.TrainingImageDetails.face_s3_path.label("training_image_s3_path"),
            )
            .join(
                orm.TrainingImageDetails,
                orm.TrainingImageDetails.image_id == orm.ClusterDetails.centroid_image_id,
            )
            .filter(orm.ClusterDetails.driver_id.in_(driver_ids))
            .order_by(orm.ClusterDetails.driver_id, orm.ClusterDetails.cluster_id)
            .distinct(orm.ClusterDetails.driver_id)
        )
        return self.db_utils.run_query_to_df(query)

    def add_images_for_labelling(self):
        image_df = self.get_images_to_be_labelled()
        if image_df.empty:
            logger.warning("No unassigned images found on the given candidate vehicles")
            return

        clusters_df = self.get_candidate_driver_reference_images(
            image_df["candidate_driver_id"].unique().tolist()
        )
        df = pd.merge(
            image_df,
            clusters_df,
            left_on="candidate_driver_id",
            right_on="driver_id",
            how="inner",
        )
        if df.empty:
            logger.warning("None of the candidate drivers have a training image available")
            return

        images_to_be_downloaded = [
            *df["inference_image_s3_path"].tolist(),
            *df["training_image_s3_path"].tolist(),
        ]
        images_to_be_downloaded = list(set(images_to_be_downloaded))
        self.threaded_downloader.download_objects(images_to_be_downloaded, self.download_folder, 20)
        df["inference_image_local_path"] = df["inference_image_s3_path"].apply(
            lambda x: os.path.join(self.download_folder, x.split("/")[-1])
        )
        df["training_image_local_path"] = df["training_image_s3_path"].apply(
            lambda x: os.path.join(self.download_folder, x.split("/")[-1])
        )

        upload_image_mapping = {}
        s3_paths = []
        for i, row in df.iterrows():
            result_image_path = os.path.join(
                self.download_folder,
                row["image_id"] + "_" + str(row["matched_cluster_id"]) + ".jpg",
            )
            self.create_comparison_image(
                row["inference_image_local_path"],
                row["training_image_local_path"],
                result_image_path,
            )
            object_key = os.path.join(self.upload_folder, result_image_path.split("/")[-1])
            upload_image_mapping[result_image_path] = object_key
            s3_paths.append(os.path.join("s3://", cfg.S3.s3_bucket, object_key))

        for result_image_path, object_key in upload_image_mapping.items():
            FileUtils.upload_to_s3(object_key, result_image_path)

        df["s3_path"] = s3_paths
        # only as many tasks as the images fill (num_tasks given at construction is just the
        # upper bound), matching AN-36974's own reasoning for using ceil() here.
        num_tasks = math.ceil(len(df) / self.num_images_per_task)
        self.register_tasks_to_database(df, num_tasks, task_type=self.task_type)
        logger.info("finished adding images for labelling")


def main(candidates_csv, num_tasks, num_relabelling_tasks, task_type):
    candidates_df = pd.read_csv(candidates_csv)
    with SingleDriverLabellingTaskGenerator(
        candidates_df, num_tasks, num_relabelling_tasks, task_type=task_type
    ) as obj:
        obj.add_images_for_labelling()
    with SingleDriverLabellingTaskGenerator(
        candidates_df, num_tasks, num_relabelling_tasks, task_type=task_type
    ) as obj:
        obj.relabel_sampled_images(task_type=task_type)


if __name__ == "__main__":
    import argparse

    logger = logging.getLogger(__name__)
    initialize_logging(
        logger, True, "offline", "prepare-single-driver-labelling-tasks", logging.INFO
    )
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--candidates_csv",
        required=True,
        help="CSV with vehicle_id,candidate_driver_id columns - output of this project's "
        "AN-37739 analysis",
    )
    parser.add_argument(
        "--num_tasks",
        type=int,
        required=True,
        help="Upper bound on how many labelling tasks get created this run",
    )
    parser.add_argument("--num_relabelling_tasks", type=int, required=True)
    parser.add_argument(
        "--task_type",
        type=int,
        default=TASK_TYPE,
        help="task_type to register these labelling tasks under (default: %(default)s). "
        "labelling_task_type_priority must not have a row for this value until "
        "frs_src/image_labelling/app.py is ready to serve these tasks to labellers.",
    )
    args = parser.parse_args()
    main(args.candidates_csv, args.num_tasks, args.num_relabelling_tasks, args.task_type)
