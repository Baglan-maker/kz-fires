import datetime as dt

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from kzfires.core.mask import MaskParams, build_mask
from kzfires.core.threat import Wind
from kzfires.retro import load_metrics, run, virtual_subscribers

T0 = dt.datetime(2024, 6, 1, 8, 0, tzinfo=dt.UTC)
H = dt.timedelta(hours=1)


def settlements():
    return gpd.GeoDataFrame(
        {"name": ["А", "Б"]},
        geometry=[Point(80.0, 50.0), Point(85.0, 45.0)],
        crs="EPSG:4326",
    )


def points():
    rows = [
        (T0, 50.0, 79.9, "N"),
        (T0, 50.002, 79.902, "N"),
        (T0 + 12 * H, 50.004, 79.904, "N20"),
        (T0 + 12 * H, 50.006, 79.906, "N20"),
        (T0 + 12 * H, 50.008, 79.908, "N20"),
        (T0 + 12 * H, 50.009, 79.909, "N20"),
    ]
    return pd.DataFrame(
        [
            {"acq_at": t, "lat": lat, "lon": lon, "satellite": s, "frp": 5.0}
            for t, lat, lon, s in rows
        ]
    )


def empty_mask():
    return build_mask(points().iloc[:0], dt.datetime(2024, 1, 1, tzinfo=dt.UTC), MaskParams())


def west_wind(_sub, _at):
    return Wind(5.0, 270.0)


def test_retro_alert_then_update_and_metrics():
    subs = virtual_subscribers(settlements(), 2, 15.0, seed=1)
    res = run(points(), empty_mask(), subs, west_wind, lambda lat, lon: None)
    sent = res.decisions[res.decisions["outcome"].isin(["alert", "update"])]
    assert list(sent["outcome"]) == ["alert", "update"]
    assert (sent["data_age_min"] == 5.0).all()
    m = load_metrics(res.decisions, n_subscribers=2, days=30)
    assert m["messages"] == 2
    assert m["subs_with_any"] == 1


def test_masked_points_never_alert():
    pts = points()
    mask = build_mask(
        pd.concat(
            [pts.assign(acq_at=pts["acq_at"] - pd.Timedelta(days=d)) for d in range(40, 400, 40)]
        ),
        dt.datetime(2024, 1, 1, tzinfo=dt.UTC),
        MaskParams(min_months=1, min_years=1),
    )
    subs = virtual_subscribers(settlements(), 2, 15.0, seed=1)
    res = run(pts, mask, subs, west_wind, lambda lat, lon: None)
    assert res.n_masked == len(pts)
    assert res.decisions.empty


def test_delay_scenario_ages_data():
    subs = virtual_subscribers(settlements(), 2, 15.0, seed=1)
    res = run(points(), empty_mask(), subs, west_wind, lambda lat, lon: None, delay_h=3)
    first = res.decisions.iloc[0]
    assert first["data_age_min"] == 185.0
