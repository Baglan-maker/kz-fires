from pathlib import Path

import pandas as pd
import pytest

from kzfires import ingest
from kzfires.sources import firms
from tests.db.test_schema import square_border

FIXTURE = Path("tests/fixtures/firms_snpp_nrt.csv")


def fake_fetch(source, bbox, days):
    raw = pd.read_csv(FIXTURE, dtype={"latitude": str, "longitude": str, "acq_time": str})
    return firms.normalize(raw, "NRT")


@pytest.fixture
def border(tmp_path):
    # синтетическая граница: в неё попадают три точки фикстуры у 40,3° 70,3°
    return square_border(tmp_path, "ingest", west=70.0, south=40.0, east=71.0, north=41.0)


def ok_status():
    return {"current_transactions": 1, "transaction_limit": 5000}


def test_run_twice_is_idempotent_and_marks_border(conn, border):
    first = ingest.run_once(conn, fake_fetch, ok_status, commit=False, border_path=border)
    second = ingest.run_once(conn, fake_fetch, ok_status, commit=False, border_path=border)
    assert first["VIIRS_SNPP_NRT"]["inserted"] == 5
    assert second["VIIRS_SNPP_NRT"]["inserted"] == 0
    assert conn.execute("SELECT count(*) FROM hotspots WHERE in_kz IS NULL").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM hotspots WHERE in_kz").fetchone()[0] == 3
    assert conn.execute("SELECT count(*) FROM ingest_runs").fetchone()[0] == 4
    assert (
        conn.execute("SELECT state FROM source_status WHERE source = 'VIIRS_SNPP_NRT'").fetchone()[
            0
        ]
        == "ok"
    )


def test_skip_when_quota_high(conn, border):
    res = ingest.run_once(
        conn,
        fake_fetch,
        lambda: {"current_transactions": 4000, "transaction_limit": 5000},
        commit=False,
        border_path=border,
    )
    assert res == {"skipped": True}
    assert conn.execute("SELECT count(*) FROM hotspots").fetchone()[0] == 0


def test_error_degrades_nothing_crashes(conn, border):
    def broken(source, bbox, days):
        raise firms.FirmsError("HTTP 503", 503)

    res = ingest.run_once(conn, broken, ok_status, commit=False, border_path=border)
    assert "error" in res["VIIRS_SNPP_NRT"]
    assert (
        conn.execute("SELECT count(*) FROM ingest_runs WHERE error IS NOT NULL").fetchone()[0] == 2
    )


def test_source_without_new_rows_is_degraded(conn, border):
    def empty(source, bbox, days):
        return fake_fetch(source, bbox, days).iloc[:0]

    res = ingest.run_once(conn, empty, ok_status, commit=False, border_path=border)
    assert res["VIIRS_SNPP_NRT"]["state"] == "degraded"
