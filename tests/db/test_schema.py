import json

import psycopg
import pytest

from kzfires.sources.border import load_border, mark_in_kz

pytestmark = pytest.mark.db

INSERT_HOTSPOT = """
INSERT INTO hotspots (product, satellite, acq_at, lat, lon, confidence, frp, daynight)
VALUES ('SP', 'N', '2023-06-08 06:53+00', %s, %s, 'n', 60.1, 'D')
ON CONFLICT DO NOTHING
RETURNING id
"""


def test_all_tables_exist(conn):
    names = {
        r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    }
    expected = {
        "country_border",
        "pipeline_runs",
        "hotspots",
        "ingest_runs",
        "source_status",
        "mask_versions",
        "mask_cells",
        "events",
        "event_hotspots",
        "subscribers",
        "settlements",
        "threat_assessments",
        "decisions",
        "alerts",
        "feedback",
        "label_sets",
        "labels",
        "eval_runs",
        "eval_results",
        "weather_cache",
    }
    assert expected <= names


def test_hotspot_unique_key_and_geom(conn):
    first = conn.execute(INSERT_HOTSPOT, ("50.55708", "80.62708")).fetchall()
    again = conn.execute(INSERT_HOTSPOT, ("50.55708", "80.62708")).fetchall()
    assert len(first) == 1
    assert again == []
    x, y = conn.execute(
        "SELECT ST_X(geom), ST_Y(geom) FROM hotspots WHERE id = %s", (first[0][0],)
    ).fetchone()
    assert (x, y) == pytest.approx((80.62708, 50.55708))


def test_coordinate_in_fifth_decimal_is_a_different_point(conn):
    conn.execute(INSERT_HOTSPOT, ("50.55708", "80.62708"))
    other = conn.execute(INSERT_HOTSPOT, ("50.55709", "80.62708")).fetchall()
    assert len(other) == 1


def test_alert_without_data_age_is_rejected(conn):
    run_id = conn.execute(
        "INSERT INTO pipeline_runs (kind, params, params_hash) VALUES ('retro', '{}', 'h') "
        "RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO events (id, run_id, first_acq_at, last_acq_at, centroid, n_points, n_passes,"
        " status, params_hash) VALUES ('e1', %s, now(), now(), ST_MakePoint(70, 48), 2, 1,"
        " 'open', 'h')",
        (run_id,),
    )
    sub_id = conn.execute(
        "INSERT INTO subscribers (zone_center, radius_km, is_virtual)"
        " VALUES (ST_SetSRID(ST_MakePoint(70.01, 48.01), 4326), 15, true) RETURNING id"
    ).fetchone()[0]
    insert = (
        "INSERT INTO decisions (run_id, event_id, subscriber_id, decided_at, outcome, reason,"
        " data_age_min) VALUES (%s, 'e1', %s, now(), %s, 'test', %s)"
    )
    conn.execute(insert, (run_id, sub_id, "suppress", None))
    conn.execute(insert, (run_id, sub_id, "alert", 42.0))
    with pytest.raises(psycopg.errors.CheckViolation), conn.transaction():
        conn.execute(insert, (run_id, sub_id, "alert", None))


def square_border(tmp_path, sha, west=70.0, south=45.0, east=72.0, north=47.0):
    ring = [[west, south], [east, south], [east, north], [west, north], [west, south]]
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"source": "test square", "source_sha256": sha},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        ],
    }
    path = tmp_path / f"border_{sha}.geojson"
    path.write_text(json.dumps(fc))
    return path


def test_mark_in_kz(conn, tmp_path):
    load_border(conn, square_border(tmp_path, "a"))
    conn.execute(INSERT_HOTSPOT, ("46.00000", "71.00000"))
    conn.execute(INSERT_HOTSPOT, ("46.00000", "75.00000"))
    assert mark_in_kz(conn) == 2
    rows = dict(conn.execute("SELECT lon::float8, in_kz FROM hotspots").fetchall())
    assert rows == {71.0: True, 75.0: False}
    assert mark_in_kz(conn) == 0


def test_border_reload_requires_force_and_resets_in_kz(conn, tmp_path):
    load_border(conn, square_border(tmp_path, "a"))
    load_border(conn, square_border(tmp_path, "a"))
    conn.execute(INSERT_HOTSPOT, ("46.00000", "75.00000"))
    mark_in_kz(conn)
    wider = square_border(tmp_path, "b", east=76.0)
    with pytest.raises(ValueError):
        load_border(conn, wider)
    load_border(conn, wider, force=True)
    assert conn.execute("SELECT in_kz FROM hotspots").fetchone()[0] is None
    mark_in_kz(conn)
    assert conn.execute("SELECT in_kz FROM hotspots").fetchone()[0] is True
