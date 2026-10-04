"""Сборка точек в события по расстоянию и времени, жизненный цикл события."""

import datetime as dt
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd


@dataclass(frozen=True)
class ClusterParams:
    eps_km: float = 2.0
    tau_h: float = 12.0
    min_samples: int = 1
    close_after_h: float = 48.0


@dataclass
class Event:
    id: str
    first_acq_at: dt.datetime
    last_acq_at: dt.datetime
    centroid_lat: float
    centroid_lon: float
    n_points: int
    n_passes: int
    max_frp: float
    status: Literal["open", "closed"] = "open"
    hotspot_ids: list[int] = field(default_factory=list)


def cluster(points: pd.DataFrame, open_events: list[Event], params: ClusterParams) -> list[Event]:
    """Присоединяет новые точки к открытым событиям или заводит новые.

    Те же входные данные дают те же события и те же id.
    """
    raise NotImplementedError
