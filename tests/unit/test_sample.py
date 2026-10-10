import datetime as dt

import numpy as np
import pandas as pd
import pytest

from kzfires.eval import sample
from kzfires.eval.sample import (
    TODO_COLUMNS,
    assign_stratum,
    draw,
    period_of,
    read_archive,
    split_quota,
    to_todo,
)


@pytest.mark.parametrize(
    "month, expected",
    [(3, None), (4, "P1"), (6, "P1"), (7, "P2"), (8, "P2"), (9, "P3"), (10, "P3"), (11, None)],
)
def test_period_of(month, expected):
    assert period_of(month) == expected


@pytest.mark.parametrize(
    "region, wc, km, expected",
    [
        ("KZ-MAN", 10, 1.0, "S1"),  # запад важнее леса и посёлка
        ("KZ-ATY", None, None, "S1"),
        ("KZ-AKM", 30, 1.0, "S2"),
        ("KZ-KUS", 20, None, "S2"),
        ("KZ-AKM", 40, 100.0, "S3"),
        ("KZ-AKM", 10, 100.0, "S4"),  # лес на севере не степь и не пашня
        ("KZ-VOS", 10, 1.0, "S4"),  # лес важнее посёлка
        ("KZ-VOS", 30, 4.9, "S5"),
        ("KZ-VOS", 30, 5.0, "S5"),
        ("KZ-VOS", 30, 5.1, "S6"),
        ("KZ-SEV", 40, 50.0, "S6"),  # Северо-Казахстанская не входит в «север» страт
        (None, None, None, "S6"),
    ],
)
def test_assign_stratum_priority(region, wc, km, expected):
    assert assign_stratum(region, wc, km) == expected


@pytest.mark.parametrize(
    "n, expected", [(25, [9, 8, 8]), (20, [7, 7, 6]), (15, [5, 5, 5]), (10, [4, 3, 3])]
)
def test_split_quota(n, expected):
    assert split_quota(n, 3) == expected
    assert sum(split_quota(n, 3)) == n


MONTH_OF = {"P1": 5, "P2": 7, "P3": 9}


def synthetic_frame(per_cell: int = 40, seed: int = 1, cells=None) -> pd.DataFrame:
    """Точки разнесены на десятки километров и по дням: правило кластера им не мешает."""
    rng = np.random.default_rng(seed)
    rows = []
    cells = cells or [(s, p) for s in sample.STRATA for p in sample.PERIODS]
    for s, p in cells:
        for i in range(per_cell):
            lat = 42.0 + rng.uniform(0, 12)
            lon = 50.0 + rng.uniform(0, 35)
            at = dt.datetime(2023, MONTH_OF[p], 1 + i % 28, 8, 0, tzinfo=dt.UTC)
            rows.append(
                {
                    "key": f"{s}|{p}|{i}",
                    "stratum": s,
                    "period": p,
                    "acq_at": pd.Timestamp(at),
                    "lat_f": lat,
                    "lon_f": lon,
                    "satellite": "N",
                    "lat": f"{lat:.5f}",
                    "lon": f"{lon:.5f}",
                }
            )
    return pd.DataFrame(rows)


def test_draw_meets_quotas_per_stratum_and_period():
    selected, shortfall = draw(synthetic_frame(), sample.DEV_QUOTAS, seed=7)
    assert shortfall == {}
    counts = selected.groupby("stratum").size().to_dict()
    assert counts == sample.DEV_QUOTAS
    by_cell = selected.groupby(["stratum", "period"]).size()
    for s, q in sample.DEV_QUOTAS.items():
        assert [by_cell[(s, p)] for p in sample.PERIODS] == split_quota(q, 3)


def test_draw_is_deterministic_for_seed():
    frame = synthetic_frame()
    a, _ = draw(frame, sample.DEV_QUOTAS, seed=7)
    b, _ = draw(frame, sample.DEV_QUOTAS, seed=7)
    c, _ = draw(frame, sample.DEV_QUOTAS, seed=8)
    assert a["key"].tolist() == b["key"].tolist()
    assert a["key"].tolist() != c["key"].tolist()


def test_draw_takes_at_most_two_points_from_one_fire():
    frame = synthetic_frame()
    fire_at = pd.Timestamp("2023-05-10 08:00", tz="UTC")
    fire = pd.DataFrame(
        {
            "key": [f"fire|{i}" for i in range(300)],
            "stratum": "S1",
            "period": "P1",
            "acq_at": [fire_at + pd.Timedelta(minutes=i) for i in range(300)],
            "lat_f": 46.0 + np.linspace(0, 0.01, 300),
            "lon_f": 52.0 + np.linspace(0, 0.01, 300),
            "satellite": "N",
            "lat": "46.0",
            "lon": "52.0",
        }
    )
    # большой пожар вытесняет обычные точки страты: без правила он занял бы всю квоту
    frame = pd.concat(
        [frame[frame["stratum"] != "S1"], fire, frame[frame["stratum"] == "S1"].head(30)]
    )
    selected, _ = draw(frame.reset_index(drop=True), sample.DEV_QUOTAS, seed=3)
    assert selected["key"].str.startswith("fire|").sum() <= 2


def test_draw_fills_empty_period_from_other_periods_of_same_stratum():
    cells = [(s, p) for s in sample.STRATA for p in sample.PERIODS if not (s == "S4" and p == "P3")]
    selected, shortfall = draw(synthetic_frame(cells=cells), sample.DEV_QUOTAS, seed=5)
    s4 = selected[selected["stratum"] == "S4"]
    assert len(s4) == sample.DEV_QUOTAS["S4"]
    assert (s4["period"] == "P3").sum() == 0
    assert shortfall == {}


def test_draw_reports_shortfall_when_stratum_is_too_small():
    frame = synthetic_frame()
    s4 = frame[frame["stratum"] == "S4"].head(3)
    frame = pd.concat([frame[frame["stratum"] != "S4"], s4]).reset_index(drop=True)
    selected, shortfall = draw(frame, sample.DEV_QUOTAS, seed=5)
    assert shortfall == {"S4": sample.DEV_QUOTAS["S4"] - 3}
    assert (selected["stratum"] == "S4").sum() == 3


def test_draw_never_takes_excluded_points():
    frame = synthetic_frame(per_cell=10)
    excluded = frozenset(frame.loc[frame["stratum"] == "S1", "key"].head(20))
    selected, _ = draw(frame, sample.DEV_QUOTAS, seed=2, exclude=excluded)
    assert not selected["key"].isin(excluded).any()


def test_todo_has_only_public_columns_and_sequential_ids():
    frame = synthetic_frame()
    frame["fire_type"] = 2
    frame["confidence"] = "h"
    selected, _ = draw(frame, sample.DEV_QUOTAS, seed=7)
    todo = to_todo(selected, "dev")
    assert list(todo.columns) == TODO_COLUMNS
    assert todo["label_id"].tolist()[:3] == ["dev-001", "dev-002", "dev-003"]
    assert todo["acq_at"].str.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$").all()


def test_read_archive_keeps_csv_coordinates_and_parses_time(tmp_path):
    header = (
        "latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,"
        "confidence,version,bright_ti5,frp,daynight,type\n"
    )
    (tmp_path / "viirs-snpp_2023_Kazakhstan.csv").write_text(
        header + "50.55674,80.62470,330,0.4,0.5,2023-06-08,653,N,VIIRS,h,2,290,103.4,D,0\n"
    )
    (tmp_path / "viirs-jpss1_2023_Kazakhstan.csv").write_text(
        header + "50.55708,80.62708,320,0.4,0.5,2023-06-08,0603,N20,VIIRS,h,2,290,11.8,D,2\n"
    )
    df = read_archive(2023, tmp_path)
    assert df["lat"].tolist() == ["50.55674", "50.55708"]
    assert df["lon"].tolist() == ["80.62470", "80.62708"]  # нуль в конце не теряется
    assert df["acq_at"].dt.strftime("%H:%M").tolist() == ["06:53", "06:03"]
    assert str(df["acq_at"].dt.tz) == "UTC"
    assert df["key"].iloc[0] == "N|2023-06-08T06:53Z|50.55674|80.62470"
    assert df["fire_type"].tolist() == [0, 2]
