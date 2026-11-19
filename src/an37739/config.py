"""Cutoff parameters swept across AN-37739's single-driver-vehicle backtest.

A vehicle qualifies as "single driver" for a given cutoff when, over the last
`lookback_weeks` weeks of vehicle_driver_history, one driver accounts for at least
`min_images` images AND at least `min_share_pct`% of all images recognized on that vehicle.
"""
from dataclasses import dataclass
from itertools import product
from typing import List


@dataclass(frozen=True)
class Cutoff:
    lookback_weeks: int
    min_images: int
    min_share_pct: float

    @property
    def label(self) -> str:
        return f"{self.lookback_weeks}wk_min{self.min_images}img_{self.min_share_pct:.0f}pct"


# Starting grid for the ticket's "report a few cut-offs" ask. Prune once the coverage step
# (an37739.coverage) shows which combinations actually produce enough qualifying vehicles to
# be worth a precision pass - no point sweeping share thresholds a tenant's data never reaches.
LOOKBACK_WEEKS_OPTIONS = (2, 4, 8)
MIN_IMAGES_OPTIONS = (5, 10, 20)
MIN_SHARE_PCT_OPTIONS = (80.0, 90.0, 95.0)


def default_cutoff_sweep() -> List[Cutoff]:
    return [
        Cutoff(lookback_weeks=weeks, min_images=images, min_share_pct=share)
        for weeks, images, share in product(
            LOOKBACK_WEEKS_OPTIONS, MIN_IMAGES_OPTIONS, MIN_SHARE_PCT_OPTIONS
        )
    ]
