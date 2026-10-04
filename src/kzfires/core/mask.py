"""Маска постоянных источников тепла (факелы, заводы, ТЭС)."""

import datetime as dt
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class MaskParams:
    cell_size_m: int = 1000
    min_detections: int = 5
    buffer_cells: int = 0


class Mask:
    def contains(self, lat: float, lon: float) -> bool:
        raise NotImplementedError


def build_mask(hotspots: pd.DataFrame, cutoff: dt.datetime, params: MaskParams) -> Mask:
    """Маска по детекциям строго до `cutoff`.

    Результат не зависит от строк с acq_at >= cutoff.
    """
    raise NotImplementedError
