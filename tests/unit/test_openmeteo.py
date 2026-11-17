import datetime as dt
import json
from pathlib import Path

import pytest

from kzfires.sources import openmeteo as om

FIXTURE = Path("tests/fixtures/openmeteo_abai_20230608.json")


def series():
    return om.parse_hourly(json.loads(FIXTURE.read_text()))


def test_cell_center():
    assert om.cell_of(50.557, 80.627) == (50.625, 80.625)
    assert om.cell_of(49.99, 79.99) == (49.875, 79.875)


def test_wind_at_takes_hour_floor():
    w = om.wind_at(series(), dt.datetime(2023, 6, 8, 6, 53, tzinfo=dt.UTC))
    assert w.speed_ms == pytest.approx(4.24)
    assert w.from_deg == pytest.approx(223)


def test_wind_outside_series_is_unknown():
    w = om.wind_at(series(), dt.datetime(2023, 6, 10, 6, 0, tzinfo=dt.UTC))
    assert w == om.Wind(None, None)
