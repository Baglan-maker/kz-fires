from pathlib import Path

import pandas as pd

from kzfires.sources import firms

FIXTURE = Path("tests/fixtures/firms_snpp_nrt.csv")


def frame():
    return firms.normalize(pd.read_csv(FIXTURE, dtype={"latitude": str, "longitude": str}), "NRT")


def test_second_load_inserts_nothing(conn):
    df = frame()
    assert firms.load_hotspots(conn, df, "NRT") == 5
    assert firms.load_hotspots(conn, df, "NRT") == 0
    n = conn.execute("SELECT count(*) FROM hotspots").fetchone()[0]
    assert n == 5


def test_first_seen_only_for_nrt(conn):
    df = frame()
    firms.load_hotspots(conn, df, "NRT")
    firms.load_hotspots(conn, df, "SP")
    rows = conn.execute(
        "SELECT product, count(first_seen_at) FROM hotspots GROUP BY product ORDER BY product"
    ).fetchall()
    assert rows == [("NRT", 5), ("SP", 0)]


def test_coordinates_kept_exactly(conn):
    firms.load_hotspots(conn, frame(), "NRT")
    lat, lon = conn.execute(
        "SELECT lat::text, lon::text FROM hotspots ORDER BY acq_at, lat LIMIT 1"
    ).fetchone()
    assert (lat, lon) == ("47.86538", "87.13970")
