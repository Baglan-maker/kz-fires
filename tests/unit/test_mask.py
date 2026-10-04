import datetime as dt

import numpy as np
import pandas as pd
import pytest

from kzfires.core.mask import MaskParams, build_mask

pytestmark = pytest.mark.xfail(raises=NotImplementedError, reason="ещё не реализовано")

CUTOFF = dt.datetime(2023, 1, 1, tzinfo=dt.UTC)
OLD_FLARE = (47.10, 51.90)  # синтетика: горит каждый месяц 2019–2022
NEW_PLANT = (45.30, 52.70)  # синтетика: появляется только в 2023


def synthetic_hotspots(seed: int = 20261005) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []

    def add(lat, lon, at, jitter_deg=0.002):
        rows.append(
            {
                "acq_at": at,
                "lat": lat + rng.normal(0, jitter_deg),
                "lon": lon + rng.normal(0, jitter_deg),
                "satellite": "N",
                "frp": float(rng.uniform(1, 30)),
                "confidence": "n",
                "daynight": "N",
            }
        )

    for year in range(2019, 2023):
        for month in range(1, 13):
            add(*OLD_FLARE, dt.datetime(year, month, 10, 21, 0, tzinfo=dt.UTC))
    for month in range(1, 13):
        for day in (5, 15, 25):
            add(*NEW_PLANT, dt.datetime(2023, month, day, 21, 0, tzinfo=dt.UTC))
    # разовые пожары: каждый горит один день и больше не повторяется
    for _ in range(300):
        at = dt.datetime(2019, 1, 1, tzinfo=dt.UTC) + dt.timedelta(
            days=float(rng.uniform(0, 5 * 365))
        )
        add(float(rng.uniform(42, 54)), float(rng.uniform(50, 85)), at, jitter_deg=0.0)
    return pd.DataFrame(rows)


def probe_points(df: pd.DataFrame) -> list[tuple[float, float]]:
    pts = list(zip(df["lat"], df["lon"], strict=True))
    pts += [OLD_FLARE, NEW_PLANT]
    return pts


def test_mask_ignores_rows_after_cutoff():
    full = synthetic_hotspots()
    before = full[full["acq_at"] < CUTOFF]
    m_full = build_mask(full, CUTOFF, MaskParams())
    m_before = build_mask(before, CUTOFF, MaskParams())
    for lat, lon in probe_points(full):
        assert m_full.contains(lat, lon) == m_before.contains(lat, lon), (lat, lon)


def test_source_seen_only_after_cutoff_is_not_masked():
    m = build_mask(synthetic_hotspots(), CUTOFF, MaskParams())
    assert not m.contains(*NEW_PLANT)


def test_old_persistent_source_is_masked():
    m = build_mask(synthetic_hotspots(), CUTOFF, MaskParams())
    assert m.contains(*OLD_FLARE)
