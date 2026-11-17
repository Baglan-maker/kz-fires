"""Сборка точек в события по расстоянию и времени, жизненный цикл события.

Две точки связаны, если между ними не больше eps_km и не больше tau_h.
Событие — компонента связности (как DBSCAN с min_samples = 1 в пространстве-времени).
Точки обрабатываются в фиксированном порядке, поэтому результат не зависит
от того, в каком порядке они пришли из API.
"""

import datetime as dt
import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Literal

import pandas as pd

from kzfires.core.geo import haversine_km


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
    points: list[tuple[float, float]] = field(default_factory=list)
    times: list[dt.datetime] = field(default_factory=list)
    passes: set[tuple[str, dt.datetime]] = field(default_factory=set, repr=False)


def event_id(acq_at: dt.datetime, lat: float, lon: float) -> str:
    return f"{acq_at:%Y%m%dT%H%M}_{lat:.5f}_{lon:.5f}"


class _Grid:
    """Ячейки примерно eps×eps км: соседей ищем только в 3×3 ячейках."""

    def __init__(self, eps_km: float):
        self.eps = eps_km
        self.cells: dict[tuple[int, int], list[tuple]] = defaultdict(list)

    def key(self, lat: float, lon: float) -> tuple[int, int]:
        # ячейка по долготе должна быть не уже eps на всех широтах Казахстана (40–56°),
        # поэтому градус долготы считаем по cos(60°): короче настоящего везде
        ky = math.floor(lat * 111.2 / self.eps)
        kx = math.floor(lon * 111.2 * 0.5 / self.eps)
        return ky, kx

    def add(self, lat: float, lon: float, t: dt.datetime, ev_idx: int) -> None:
        self.cells[self.key(lat, lon)].append((lat, lon, t, ev_idx))

    def near(self, lat: float, lon: float):
        ky, kx = self.key(lat, lon)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                yield from self.cells.get((ky + dy, kx + dx), ())


def _copy(e: Event) -> Event:
    return Event(
        e.id,
        e.first_acq_at,
        e.last_acq_at,
        e.centroid_lat,
        e.centroid_lon,
        e.n_points,
        e.n_passes,
        e.max_frp,
        e.status,
        list(e.hotspot_ids),
        list(e.points),
        list(e.times),
        set(e.passes),
    )


def _absorb(dst: Event, src: Event) -> None:
    n = dst.n_points + src.n_points
    dst.centroid_lat = (dst.centroid_lat * dst.n_points + src.centroid_lat * src.n_points) / n
    dst.centroid_lon = (dst.centroid_lon * dst.n_points + src.centroid_lon * src.n_points) / n
    dst.n_points = n
    dst.first_acq_at = min(dst.first_acq_at, src.first_acq_at)
    dst.last_acq_at = max(dst.last_acq_at, src.last_acq_at)
    dst.max_frp = max(dst.max_frp, src.max_frp)
    dst.hotspot_ids += src.hotspot_ids
    dst.points += src.points
    dst.times += src.times
    dst.passes |= src.passes
    dst.n_passes = len(dst.passes)


def cluster(
    points: pd.DataFrame,
    open_events: list[Event],
    params: ClusterParams,
    now: dt.datetime | None = None,
) -> list[Event]:
    """Присоединяет новые точки к открытым событиям или заводит новые.

    Те же входные данные дают те же события и те же id. Возвращает все события
    (и прежние, и новые), отсортированные по id.
    """
    events: list[Event | None] = [_copy(e) for e in open_events]
    grid = _Grid(params.eps_km)
    for i, e in enumerate(events):
        for (lat, lon), t in zip(e.points, e.times, strict=True):
            grid.add(lat, lon, t, i)
    alias = list(range(len(events)))

    def root(i: int) -> int:
        while alias[i] != i:
            alias[i] = alias[alias[i]]
            i = alias[i]
        return i

    tau = dt.timedelta(hours=params.tau_h)
    df = points.copy()
    if "id" not in df:
        df["id"] = -1
    df["acq_at"] = pd.to_datetime(df["acq_at"], utc=True)
    df = df.sort_values(["acq_at", "lat", "lon", "satellite", "id"], kind="stable")

    for r in df.itertuples(index=False):
        t = r.acq_at.to_pydatetime()
        lat, lon = float(r.lat), float(r.lon)
        hits = set()
        for plat, plon, pt, ev_idx in grid.near(lat, lon):
            if abs(pt - t) > tau:
                continue
            if haversine_km(lat, lon, plat, plon) <= params.eps_km:
                hits.add(root(ev_idx))

        if hits:
            # при слиянии живёт событие со старшим (самым ранним) id
            target = min(hits, key=lambda i: events[i].id)
            for other in sorted(hits - {target}):
                _absorb(events[target], events[other])
                events[other] = None
                alias[other] = target
        else:
            events.append(Event(event_id(t, lat, lon), t, t, lat, lon, 0, 0, 0.0))
            alias.append(len(events) - 1)
            target = len(events) - 1

        e = events[target]
        e.centroid_lat = (e.centroid_lat * e.n_points + lat) / (e.n_points + 1)
        e.centroid_lon = (e.centroid_lon * e.n_points + lon) / (e.n_points + 1)
        e.n_points += 1
        e.first_acq_at = min(e.first_acq_at, t)
        e.last_acq_at = max(e.last_acq_at, t)
        e.max_frp = max(e.max_frp, float(r.frp) if pd.notna(r.frp) else 0.0)
        e.hotspot_ids.append(int(r.id))
        e.points.append((lat, lon))
        e.times.append(t)
        e.passes.add((str(r.satellite), t))
        e.n_passes = len(e.passes)
        grid.add(lat, lon, t, target)

    out = [e for e in events if e is not None]
    if now is not None:
        close = dt.timedelta(hours=params.close_after_h)
        for e in out:
            if now - e.last_acq_at > close:
                e.status = "closed"
    return sorted(out, key=lambda e: e.id)
