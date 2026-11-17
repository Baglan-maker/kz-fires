from pathlib import Path

import pandas as pd

from kzfires import ingest
from kzfires.sources import firms

FIXTURE = Path("tests/fixtures/firms_snpp_nrt.csv")


def fake_fetch(source, bbox, days):
    raw = pd.read_csv(FIXTURE, dtype={"latitude": str, "longitude": str, "acq_time": str})
    return firms.normalize(raw, "NRT")


def ok_status():
    return {"current_transactions": 1, "transaction_limit": 5000}


def test_run_twice_is_idempotent_and_marks_border(conn):
    first = ingest.run_once(conn, fake_fetch, ok_status, commit=False)
    second = ingest.run_once(conn, fake_fetch, ok_status, commit=False)
    assert first["VIIRS_SNPP_NRT"]["inserted"] == 5
    assert second["VIIRS_SNPP_NRT"]["inserted"] == 0
    assert conn.execute("SELECT count(*) FROM hotspots WHERE in_kz IS NULL").fetchone()[0] == 0
    # 47.865 87.140 — Китай, 41.73 81.67 — Кыргызстан/Китай; в Казахстане из фикстуры никого
    assert conn.execute("SELECT count(*) FROM ingest_runs").fetchone()[0] == 4
    assert (
        conn.execute("SELECT state FROM source_status WHERE source = 'VIIRS_SNPP_NRT'").fetchone()[
            0
        ]
        == "ok"
    )


def test_skip_when_quota_high(conn):
    res = ingest.run_once(
        conn,
        fake_fetch,
        lambda: {"current_transactions": 4000, "transaction_limit": 5000},
        commit=False,
    )
    assert res == {"skipped": True}
    assert conn.execute("SELECT count(*) FROM hotspots").fetchone()[0] == 0


def test_error_degrades_nothing_crashes(conn):
    def broken(source, bbox, days):
        raise firms.FirmsError("HTTP 503", 503)

    res = ingest.run_once(conn, broken, ok_status, commit=False)
    assert "error" in res["VIIRS_SNPP_NRT"]
    assert (
        conn.execute("SELECT count(*) FROM ingest_runs WHERE error IS NOT NULL").fetchone()[0] == 2
    )


def test_source_without_new_rows_is_degraded(conn):
    def empty(source, bbox, days):
        return fake_fetch(source, bbox, days).iloc[:0]

    res = ingest.run_once(conn, empty, ok_status, commit=False)
    assert res["VIIRS_SNPP_NRT"]["state"] == "degraded"
