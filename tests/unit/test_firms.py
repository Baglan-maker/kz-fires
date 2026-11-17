import datetime as dt
import logging
from pathlib import Path

import pandas as pd
import pytest
import requests

from kzfires.sources import firms

FIXTURE = Path("tests/fixtures/firms_snpp_nrt.csv")
KEY = "abcdef0123456789secretkey"
BBOX = (46.0, 40.0, 88.0, 56.0)


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status_code = status
        self.text = text

    def json(self):
        import json

        return json.loads(self.text)


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls: list[str] = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def no_sleep(_):
    pass


def test_url_order_is_west_south_east_north_and_key_in_path():
    s = FakeSession([FakeResponse(200, FIXTURE.read_text())])
    firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 2, key=KEY, session=s, sleep=no_sleep)
    assert s.urls[0].endswith(f"/api/area/csv/{KEY}/VIIRS_SNPP_NRT/46.0,40.0,88.0,56.0/2")


def test_parses_and_normalizes():
    s = FakeSession([FakeResponse(200, FIXTURE.read_text())])
    df = firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert len(df) == 5
    row = df.iloc[1]
    assert row["satellite"] == "N"
    assert row["acq_at"] == pd.Timestamp("2026-10-04T07:39:00Z")
    # координаты в ключе — строкой как в CSV, без круга через float
    assert row["lat_s"] == "40.34669"
    assert row["lon_s"] == "70.27197"


def test_date_is_appended():
    s = FakeSession([FakeResponse(200, FIXTURE.read_text())])
    firms.fetch_area(
        "VIIRS_SNPP_SP", BBOX, 5, dt.date(2025, 6, 1), key=KEY, session=s, sleep=no_sleep
    )
    assert s.urls[0].endswith("/5/2025-06-01")


def test_4xx_is_not_retried_and_hides_key():
    s = FakeSession([FakeResponse(403, f"Invalid MAP_KEY {KEY}")])
    with pytest.raises(firms.FirmsError) as ei:
        firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert len(s.urls) == 1
    assert ei.value.status == 403
    assert KEY not in str(ei.value)
    assert ei.value.__cause__ is None


def test_5xx_is_retried_then_succeeds():
    s = FakeSession([FakeResponse(503, "busy"), FakeResponse(200, FIXTURE.read_text())])
    waits = []
    df = firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=waits.append)
    assert len(df) == 5
    assert waits == [2]


def test_5xx_gives_up_after_retries():
    s = FakeSession([FakeResponse(500, "x")] * 4)
    with pytest.raises(firms.FirmsError) as ei:
        firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert ei.value.status == 500
    assert len(s.urls) == 4


def test_error_text_with_200_is_an_error():
    s = FakeSession([FakeResponse(200, f"Invalid area. key={KEY}")])
    with pytest.raises(firms.FirmsError) as ei:
        firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert KEY not in str(ei.value)


def test_network_error_hides_url(caplog):
    url_err = requests.ConnectionError(f"HTTPSConnectionPool: /api/area/csv/{KEY}/x failed")
    s = FakeSession([url_err] * 4)
    caplog.set_level(logging.DEBUG)
    with pytest.raises(firms.FirmsError) as ei:
        firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert KEY not in str(ei.value)
    assert ei.value.__suppress_context__
    assert KEY not in caplog.text


def test_empty_answer_is_empty_frame():
    header = FIXTURE.read_text().splitlines()[0] + "\n"
    s = FakeSession([FakeResponse(200, header)])
    df = firms.fetch_area("VIIRS_SNPP_NRT", BBOX, 1, key=KEY, session=s, sleep=no_sleep)
    assert df.empty


def test_mapkey_status_hides_key():
    s = FakeSession([FakeResponse(200, '{"current_transactions": 7, "transaction_limit": 5000}')])
    st = firms.mapkey_status(key=KEY, session=s)
    assert st == {"current_transactions": 7, "transaction_limit": 5000}


def test_annual_archive_reader_keeps_type():
    df = firms.read_country_csv(Path("tests/fixtures/firms_snpp_sp_annual.csv"))
    assert set(df["satellite"]) == {"N"}
    assert "fire_type" in df


@pytest.mark.parametrize(
    "source, sat, product",
    [
        ("VIIRS_SNPP_NRT", "N", "NRT"),
        ("VIIRS_NOAA20_NRT", "N20", "NRT"),
        ("VIIRS_NOAA21_NRT", "N21", "NRT"),
        ("VIIRS_SNPP_SP", "N", "SP"),
        ("VIIRS_NOAA20_SP", "N20", "SP"),
    ],
)
def test_source_mapping(source, sat, product):
    assert firms.source_info(source) == (sat, product)
