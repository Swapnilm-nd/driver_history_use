"""
AN-37739 step 4: precision of the single-driver auto-assign rule.

Two tracks:
  1. `contradicted_vehicles` - free, no labelling needed: vehicles the candidate-set rule
     would have auto-assigned, where the target week's OWN vls_driver_distribution shows a
     different positive driver_id than the candidate. This only catches cases where the real
     second driver happened to get recognized that same week, so it's a floor on the true
     error rate, not the true error rate itself (see the ticket's own caveat about history
     only covering recognized drivers).
  2. `sample_for_labelling` - picks a reproducible random sample of auto-assigned (vehicle,
     driver) pairs for a human precision review. Submitting that sample to the labelling tool
     means subclassing PrecisionLabellingTaskGenerator the way
     prepare_ambiguous_labelling_tasks.py's AmbiguousLabellingTaskGenerator does (override
     get_images_to_be_labelled to source from this sample, and a pick_top1-style helper to
     choose the comparison image) - deliberately not reimplemented here, since that pipeline
     already does the S3/image-stitching/task-registration work and shouldn't be duplicated.
"""
import random
from typing import Dict, List, Tuple


def contradicted_vehicles(
    candidate_drivers: Dict[int, int],
    target_week_vls_driver_distribution: Dict[int, Dict[int, int]],
) -> Dict[int, Tuple[int, int]]:
    """{vehicle_id: (candidate_driver_id, actually_seen_driver_id)} for every candidate vehicle
    where the target week's own recognition saw a DIFFERENT positive driver_id than the one
    the rule would have assigned - a direct, computable contradiction, no labelling needed."""
    contradictions = {}
    for vehicle_id, candidate_driver_id in candidate_drivers.items():
        distribution = target_week_vls_driver_distribution.get(vehicle_id, {})
        other_positive_drivers = {
            driver_id
            for driver_id in distribution
            if driver_id > 0 and driver_id != candidate_driver_id
        }
        if other_positive_drivers:
            # Most-minutes driver, for readability in the per-vehicle report - only
            # existence matters for the contradiction count itself.
            seen_driver_id = max(other_positive_drivers, key=lambda d: distribution[d])
            contradictions[vehicle_id] = (candidate_driver_id, seen_driver_id)
    return contradictions


def sample_for_labelling(
    candidate_drivers: Dict[int, int], sample_size: int, seed: int = 37739
) -> List[Tuple[int, int]]:
    """A reproducible random sample of (vehicle_id, driver_id) pairs from the full candidate
    set, sized for a labelling-tool precision review. Fixed seed so re-running the sweep for a
    different cutoff doesn't silently redraw a fresh sample for vehicles already reviewed."""
    pairs = sorted(candidate_drivers.items())
    rng = random.Random(seed)
    return rng.sample(pairs, k=min(sample_size, len(pairs)))
