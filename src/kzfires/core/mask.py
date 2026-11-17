"""Маска постоянных источников тепла (факелы, заводы, ТЭС).

Ячейка сетки попадает в маску, если в ней горело часто, в разные месяцы
и в разные годы. Факел светит круглый год и каждый год; степной пожар —
в сезон и редко на том же месте; пал на поле повторяется по годам, но
в одни и те же месяцы.
"""

import datetime as dt
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from pyproj import Transformer

# равновеликая проекция: ячейка 1 км везде 1 км², а не сжимается к северу
_TO_EASE = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)


@dataclass(frozen=True)
class MaskParams:
    cell_size_m: int = 1000
    min_detections: int = 5
    buffer_cells: int = 0
    min_months: int = 3
    min_years: int = 2
    lookback_years: int = 3


def cells_of(lat, lon, cell_size_m: int) -> tuple[np.ndarray, np.ndarray]:
    x, y = _TO_EASE.transform(np.asarray(lon, dtype=float), np.asarray(lat, dtype=float))
    return (
        np.floor(np.asarray(x) / cell_size_m).astype(np.int64),
        np.floor(np.asarray(y) / cell_size_m).astype(np.int64),
    )


class Mask:
    def __init__(self, cells: pd.DataFrame, params: MaskParams, cutoff: dt.datetime):
        self.params = params
        self.cutoff = cutoff
        self.cells = cells.sort_values(["ix", "iy"]).reset_index(drop=True)
        b = params.buffer_cells
        keys = set(zip(self.cells["ix"].tolist(), self.cells["iy"].tolist(), strict=True))
        if b:
            keys = {
                (x + dx, y + dy)
                for x, y in keys
                for dx in range(-b, b + 1)
                for dy in range(-b, b + 1)
            }
        self._keys = keys

    def __len__(self) -> int:
        return len(self.cells)

    def contains(self, lat: float, lon: float) -> bool:
        ix, iy = cells_of([lat], [lon], self.params.cell_size_m)
        return (int(ix[0]), int(iy[0])) in self._keys

    def contains_many(self, lat, lon) -> np.ndarray:
        ix, iy = cells_of(lat, lon, self.params.cell_size_m)
        return np.fromiter(
            ((a, b) in self._keys for a, b in zip(ix.tolist(), iy.tolist(), strict=True)),
            dtype=bool,
            count=len(ix),
        )

    def describe(self) -> dict:
        return {"cutoff": self.cutoff.isoformat(), "n_cells": len(self), **asdict(self.params)}


def build_mask(hotspots: pd.DataFrame, cutoff: dt.datetime, params: MaskParams) -> Mask:
    """Маска по детекциям строго до `cutoff`.

    Результат не зависит от строк с acq_at >= cutoff.
    """
    start = cutoff.replace(year=cutoff.year - params.lookback_years)
    at = pd.to_datetime(hotspots["acq_at"], utc=True)
    sel = ((at >= start) & (at < cutoff)).to_numpy()
    # по позиции, а не по индексу: у склеенных таблиц индекс может повторяться
    win = hotspots.iloc[np.nonzero(sel)[0]]
    at = at.iloc[np.nonzero(sel)[0]]
    if win.empty:
        empty = pd.DataFrame(columns=["ix", "iy", "n_detections", "n_months", "n_years"])
        return Mask(empty, params, cutoff)
    ix, iy = cells_of(win["lat"], win["lon"], params.cell_size_m)
    df = pd.DataFrame({"ix": ix, "iy": iy, "month": at.dt.month.values, "year": at.dt.year.values})
    g = df.groupby(["ix", "iy"])
    cells = pd.DataFrame(
        {
            "n_detections": g.size(),
            "n_months": g["month"].nunique(),
            "n_years": g["year"].nunique(),
        }
    ).reset_index()
    keep = (
        (cells["n_detections"] >= params.min_detections)
        & (cells["n_months"] >= params.min_months)
        & (cells["n_years"] >= params.min_years)
    )
    return Mask(cells.loc[keep], params, cutoff)
