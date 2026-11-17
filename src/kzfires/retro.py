"""Ретро-прогон конвейера на архиве SP: маска → события → угроза → решение.

Подписчики виртуальные: населённые пункты OSM, выбранные по seed. Реальных
людей и координат здесь нет. Пролёты обрабатываются по порядку, как если бы
они приходили вживую; тревога «отправляется» через delay_h после пролёта.
"""

import datetime as dt
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from kzfires.core.decision import DecisionParams, SentMessage, decide
from kzfires.core.events import ClusterParams, Event, cluster
from kzfires.core.mask import Mask
from kzfires.core.threat import ThreatParams, Wind, Zone, assess

PROCESSING = dt.timedelta(minutes=5)


@dataclass(frozen=True)
class Subscriber:
    id: int
    name: str
    zone: Zone


@dataclass
class RetroResult:
    decisions: pd.DataFrame
    events: list[Event]
    n_points: int
    n_masked: int
    subscribers: list[Subscriber] = field(default_factory=list)


def unique_settlements(settlements):
    """В OSM один посёлок бывает и точкой, и полигоном: склеиваем по месту и имени."""
    key = pd.DataFrame(
        {
            "lat": settlements.geometry.y.round(2),
            "lon": settlements.geometry.x.round(2),
            "name": settlements["name"].fillna(""),
        }
    )
    return settlements.loc[~key.duplicated().to_numpy()].reset_index(drop=True)


def virtual_subscribers(settlements, n: int, radius_km: float, seed: int) -> list[Subscriber]:
    settlements = unique_settlements(settlements)
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(settlements), size=n, replace=False))
    out = []
    for k, i in enumerate(idx):
        row = settlements.iloc[int(i)]
        # центр зоны округлён до 0,01°, как у настоящих подписчиков
        zone = Zone(round(row.geometry.y, 2), round(row.geometry.x, 2), radius_km)
        out.append(Subscriber(k, str(row.get("name") or ""), zone))
    return out


def _near(
    sub_lat: np.ndarray, sub_lon: np.ndarray, lat: float, lon: float, km: float
) -> np.ndarray:
    """Грубый отбор подписчиков в квадрате: точная дистанция считается в assess."""
    dlat = km / 111.0
    dlon = km / (111.0 * np.cos(np.radians(lat)))
    return np.nonzero((np.abs(sub_lat - lat) <= dlat) & (np.abs(sub_lon - lon) <= dlon))[0]


def run(
    points: pd.DataFrame,
    mask: Mask,
    subscribers: list[Subscriber],
    wind_fn,
    land_fn,
    cluster_params: ClusterParams | None = None,
    threat_params: ThreatParams | None = None,
    decision_params: DecisionParams | None = None,
    delay_h: float = 0.0,
) -> RetroResult:
    """`wind_fn(subscriber, at) -> Wind`, `land_fn(lat, lon) -> int | None`."""
    cluster_params = cluster_params or ClusterParams()
    threat_params = threat_params or ThreatParams()
    decision_params = decision_params or DecisionParams()
    pts = points.copy()
    pts["acq_at"] = pd.to_datetime(pts["acq_at"], utc=True)
    pts = pts.sort_values(["acq_at", "satellite", "lat", "lon"], kind="stable").reset_index(
        drop=True
    )
    pts["id"] = np.arange(len(pts))
    in_mask = mask.contains_many(pts["lat"], pts["lon"])
    # точки в маске не идут в события: так конвейер работает вживую
    live = pts[~in_mask]

    sub_lat = np.array([s.zone.lat for s in subscribers])
    sub_lon = np.array([s.zone.lon for s in subscribers])
    max_r = max((s.zone.radius_km for s in subscribers), default=0.0)
    history: dict[int, list[SentMessage]] = {s.id: [] for s in subscribers}
    open_events: list[Event] = []
    closed: list[Event] = []
    rows = []
    delay = dt.timedelta(hours=delay_h)

    for t, batch in live.groupby("acq_at", sort=True):
        t = t.to_pydatetime()
        events = cluster(batch, open_events, cluster_params, now=t)
        closed += [e for e in events if e.status == "closed"]
        open_events = [e for e in events if e.status == "open"]
        touched = set(batch["id"])
        now = t + delay + PROCESSING
        for e in open_events:
            if touched.isdisjoint(e.hotspot_ids):
                continue
            # подписчики, до которых событие может дотянуться с запасом на размер события
            for j in _near(sub_lat, sub_lon, e.centroid_lat, e.centroid_lon, max_r + 20.0):
                sub = subscribers[j]
                threat = assess(
                    e,
                    sub.zone,
                    wind_fn(sub, now),
                    land_fn(e.centroid_lat, e.centroid_lon),
                    threat_params,
                )
                if threat.reasons.get("out_of_radius"):
                    continue
                d = decide(e, threat, history[sub.id], now, decision_params)
                if d.outcome in ("alert", "update"):
                    history[sub.id].append(SentMessage(e.id, now, threat.level, e.n_points))
                rows.append(
                    {
                        "at": now,
                        "acq_at": t,
                        "subscriber_id": sub.id,
                        "event_id": e.id,
                        "outcome": d.outcome,
                        "reason": d.reason,
                        "data_age_min": d.data_age_min,
                        "level": threat.level,
                        "wind_class": threat.wind_class,
                        "dist_km": threat.dist_km,
                        "n_points": e.n_points,
                        "event_lat": e.centroid_lat,
                        "event_lon": e.centroid_lon,
                        "first_acq_at": e.first_acq_at,
                    }
                )
    return RetroResult(
        pd.DataFrame(rows),
        closed + open_events,
        n_points=len(pts),
        n_masked=int(in_mask.sum()),
        subscribers=subscribers,
    )


def load_metrics(decisions: pd.DataFrame, n_subscribers: int, days: int) -> dict:
    """Нагрузка на подписчика: не медиана по суткам (она почти всегда 0)."""
    sent = decisions[decisions["outcome"].isin(["alert", "update"])].copy()
    if sent.empty:
        return {"messages": 0, "share_sub_days_with_alert": 0.0}
    local = pd.to_datetime(sent["at"]).dt.tz_convert("Asia/Almaty").dt.date
    per_day = sent.assign(day=local).groupby(["subscriber_id", "day"]).size()
    per_sub = sent.groupby("subscriber_id").size().reindex(range(n_subscribers), fill_value=0)
    return {
        "messages": int(len(sent)),
        "alerts": int((sent["outcome"] == "alert").sum()),
        "updates": int((sent["outcome"] == "update").sum()),
        "share_sub_days_with_alert": len(per_day) / (n_subscribers * days),
        "p95_per_day_when_any": float(np.percentile(per_day, 95)),
        "max_per_day": int(per_day.max()),
        "season_per_sub_median": float(per_sub.median()),
        "season_per_sub_p95": float(np.percentile(per_sub, 95)),
        "subs_with_any": int((per_sub > 0).sum()),
    }


def no_wind(_sub, _at) -> Wind:
    return Wind(None, None)
