"""Команда retro: ретро-прогон по замороженной конфигурации и разбор Абая 2023."""

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd

from kzfires.core.decision import DecisionParams
from kzfires.core.events import ClusterParams
from kzfires.core.geo import haversine_km
from kzfires.core.mask import build_mask
from kzfires.core.threat import ThreatParams, Zone
from kzfires.eval.filter_eval import load_config
from kzfires.retro import Subscriber, load_metrics, run, unique_settlements, virtual_subscribers
from kzfires.sources import archive
from kzfires.sources import openmeteo as om
from kzfires.sources.osm import load_settlements

OUT = Path("data/retro")
ABAI_FIRST = (50.557, 80.627)


class WindCache:
    """Ветер на ячейке подписчика; год целиком одним запросом."""

    def __init__(self):
        self.series: dict = {}

    def __call__(self, sub: Subscriber, at: dt.datetime):
        key = (*om.cell_of(sub.zone.lat, sub.zone.lon), at.year)
        if key not in self.series:
            self.series[key] = om.year_series(sub.zone.lat, sub.zone.lon, at.year)
        return om.wind_at(self.series[key], at)


def _params(cfg: dict):
    return (
        ClusterParams(**cfg["events"]),
        ThreatParams(**cfg["threat"]),
        DecisionParams(**cfg["decision"]),
    )


def _mask_for(year: int, params):
    snpp = archive.load(range(year - params.lookback_years, year), satellites=("N",))
    return build_mask(snpp, dt.datetime(year, 1, 1, tzinfo=dt.UTC), params)


def season(year: int, delays=(0.0, 3.0)) -> dict:
    cfg, mask_params = load_config()
    cp, tp, dp = _params(cfg)
    sub_cfg = cfg["subscribers"]
    subs = virtual_subscribers(
        load_settlements(), sub_cfg["n"], sub_cfg["radius_km"], sub_cfg["seed"]
    )
    mask = _mask_for(year, mask_params)
    pts = archive.load([year])
    wind = WindCache()
    out = {"year": year, "mask_params_hash": cfg["mask_params_hash"], "n_subscribers": len(subs)}
    days = 366 if year % 4 == 0 else 365
    for delay in delays:
        res = run(pts, mask, subs, wind, lambda lat, lon: None, cp, tp, dp, delay_h=delay)
        OUT.mkdir(parents=True, exist_ok=True)
        res.decisions.to_pickle(OUT / f"decisions_{year}_d{delay:g}.pkl")
        m = load_metrics(res.decisions, len(subs), days)
        m["n_points"], m["n_masked"], m["n_events"] = res.n_points, res.n_masked, len(res.events)
        m["suppress_reasons"] = (
            res.decisions.loc[res.decisions["outcome"] == "suppress", "reason"]
            .value_counts()
            .to_dict()
        )
        out[f"delay_{delay:g}h"] = m
    (OUT / f"metrics_{year}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def abai() -> pd.DataFrame:
    cfg, mask_params = load_config()
    cp, tp, dp = _params(cfg)
    mask = _mask_for(2023, mask_params)
    pts = archive.load([2023])
    pts = pts[(pts["acq_at"] >= "2023-06-07") & (pts["acq_at"] < "2023-06-11")]
    pts = pts[pts["lat"].between(49.5, 51.5) & pts["lon"].between(79.0, 82.5)]
    st = unique_settlements(load_settlements())
    d = np.array([haversine_km(*ABAI_FIRST, g.y, g.x) for g in st.geometry])
    near = st[d <= 30.0]
    subs = [
        Subscriber(k, str(r["name"]), Zone(round(r.geometry.y, 2), round(r.geometry.x, 2), 15.0))
        for k, (_, r) in enumerate(near.iterrows())
    ]
    wind = WindCache()
    rows = []
    for delay in (0.0, 3.0):
        res = run(pts, mask, subs, wind, lambda lat, lon: None, cp, tp, dp, delay_h=delay)
        dec = res.decisions
        first = dec[dec["outcome"] == "alert"].sort_values("at").groupby("subscriber_id").first()
        for sid, r in first.iterrows():
            rows.append(
                {
                    "delay_h": delay,
                    "settlement": subs[sid].name,
                    "alert_utc": r["at"],
                    "dist_km": r["dist_km"],
                    "wind_class": r["wind_class"],
                    "level": r["level"],
                    "data_age_min": r["data_age_min"],
                    "n_points": r["n_points"],
                }
            )
        print(
            f"Абай, задержка {delay:g} ч: тревога у {len(first)} из {len(subs)} посёлков "
            f"в 30 км; сообщений {int(dec['outcome'].isin(['alert', 'update']).sum())}"
        )
    df = pd.DataFrame(rows).sort_values(["delay_h", "alert_utc"])
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "abai_2023.csv", index=False)
    return df


def abai_demo_messages(max_messages: int = 3) -> list[str]:
    """Тексты тревог для ближайшего к пожару посёлка в сценарии задержки 3 ч.

    Зона подписчика в демо — посёлок у пожара, а не его настоящая точка:
    пожар 2023 года был в другом месте.
    """
    from kzfires.bot import texts_ru as t
    from kzfires.core.geo import bearing_deg

    cfg, mask_params = load_config()
    cp, tp, dp = _params(cfg)
    mask = _mask_for(2023, mask_params)
    pts = archive.load([2023])
    pts = pts[(pts["acq_at"] >= "2023-06-07") & (pts["acq_at"] < "2023-06-11")]
    pts = pts[pts["lat"].between(49.5, 51.5) & pts["lon"].between(79.0, 82.5)]
    st = unique_settlements(load_settlements())
    d = np.array([haversine_km(*ABAI_FIRST, g.y, g.x) for g in st.geometry])
    row = st.iloc[int(np.argsort(d)[0])]
    sub = Subscriber(
        0, str(row["name"]), Zone(round(row.geometry.y, 2), round(row.geometry.x, 2), 15.0)
    )
    wind = WindCache()
    res = run(pts, mask, [sub], wind, lambda lat, lon: None, cp, tp, dp, delay_h=3.0)
    sent = res.decisions[res.decisions["outcome"].isin(["alert", "update"])].head(max_messages)
    out = []
    for r in sent.itertuples():
        w = wind(sub, r.at.to_pydatetime())
        v = t.AlertView(
            dist_km=r.dist_km,
            bearing_deg=bearing_deg(sub.zone.lat, sub.zone.lon, r.event_lat, r.event_lon),
            acq_at=r.acq_at,
            data_age_min=r.data_age_min,
            land_cover=None,
            wind_from_deg=w.from_deg,
            wind_speed_ms=w.speed_ms,
            wind_class=r.wind_class,
            lat=r.event_lat,
            lon=r.event_lon,
            is_update=r.outcome == "update",
            demo_note=f"ДЕМО: воспроизведение пожара 8 июня 2023 для посёлка {sub.name}.",
        )
        out.append(t.render_alert(v))
    return out
