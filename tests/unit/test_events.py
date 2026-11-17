import datetime as dt

import pandas as pd

from kzfires.core.events import ClusterParams, cluster

T0 = dt.datetime(2023, 6, 8, 6, 0, tzinfo=dt.UTC)
P = ClusterParams()
H = dt.timedelta(hours=1)


def pts(rows) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": i, "acq_at": t, "lat": lat, "lon": lon, "satellite": sat, "frp": 5.0 + i}
            for i, (t, lat, lon, sat) in enumerate(rows)
        ]
    )


ONE_FIRE_3_DAYS = [
    (T0, 50.000, 80.000, "N"),
    (T0, 50.005, 80.004, "N"),
    (T0 + 10 * H, 50.010, 80.010, "N20"),
    (T0 + 20 * H, 50.020, 80.020, "N"),
    (T0 + 30 * H, 50.030, 80.030, "N20"),
    (T0 + 40 * H, 50.035, 80.040, "N"),
    (T0 + 50 * H, 50.040, 80.050, "N20"),
]


def test_one_fire_over_three_days_is_one_event():
    events = cluster(pts(ONE_FIRE_3_DAYS), [], P)
    assert len(events) == 1
    e = events[0]
    assert e.n_points == 7
    assert e.n_passes == 6
    assert e.first_acq_at == T0
    assert e.last_acq_at == T0 + 50 * H


def test_two_fires_30_km_apart_are_two_events():
    rows = [(T0, 50.0, 80.0, "N"), (T0, 50.0, 80.42, "N")]  # ≈ 30 км
    assert len(cluster(pts(rows), [], P)) == 2


def test_far_in_time_is_new_event():
    rows = [(T0, 50.0, 80.0, "N"), (T0 + 30 * H, 50.0, 80.0, "N")]
    assert len(cluster(pts(rows), [], P)) == 2


def test_deterministic_ids_and_order_independent():
    df = pts(ONE_FIRE_3_DAYS + [(T0, 50.0, 80.42, "N")])
    a = cluster(df, [], P)
    b = cluster(df.sample(frac=1.0, random_state=7), [], P)
    assert [(e.id, e.n_points) for e in a] == [(e.id, e.n_points) for e in b]


def test_id_comes_from_earliest_point():
    e = cluster(pts(ONE_FIRE_3_DAYS), [], P)[0]
    assert e.id.startswith("20230608T0600")


def test_incremental_equals_batch():
    df = pts(ONE_FIRE_3_DAYS)
    batch = cluster(df, [], P)
    first = cluster(df.iloc[:3], [], P)
    inc = cluster(df.iloc[3:], first, P)
    assert [(e.id, e.n_points) for e in inc] == [(e.id, e.n_points) for e in batch]


def test_late_point_does_not_split_event():
    df = pts(ONE_FIRE_3_DAYS)
    first = cluster(df.drop(index=2), [], P)
    # точка 10-го часа приходит последней, уже после точек 20–50 часов
    after = cluster(df.loc[[2]], first, P)
    assert len(after) == 1
    assert after[0].n_points == 7


def test_bridging_point_merges_and_keeps_older_id():
    rows = [(T0, 50.0, 80.0, "N"), (T0 + H, 50.0, 80.05, "N")]  # ≈ 3,6 км: два события
    two = cluster(pts(rows), [], P)
    assert len(two) == 2
    bridge = pts([(T0 + 2 * H, 50.0, 80.025, "N20")])
    bridge["id"] = 99
    merged = cluster(bridge, two, P)
    assert len(merged) == 1
    assert merged[0].id == min(e.id for e in two)
    assert merged[0].n_points == 3


def test_event_closes_after_quiet_period():
    events = cluster(pts([(T0, 50.0, 80.0, "N")]), [], P, now=T0 + 49 * H)
    assert events[0].status == "closed"
