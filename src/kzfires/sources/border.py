"""Граница Казахстана в БД и флаг in_kz у точек."""

import json
from pathlib import Path

import psycopg

BORDER_PATH = Path("data/border/kz_ne10m.geojson")
BORDER_ID = "kz"


def load_border(conn: psycopg.Connection, path: Path = BORDER_PATH, force: bool = False) -> None:
    """Загружает полигон из GeoJSON (scripts/build_border.py).

    Другая граница поверх уже загруженной меняет смысл in_kz у старых точек,
    поэтому без force это ошибка, а с force in_kz сбрасывается для пересчёта.
    """
    feature = json.loads(path.read_text())["features"][0]
    props = feature["properties"]
    row = conn.execute(
        "SELECT source_sha256 FROM country_border WHERE id = %s", (BORDER_ID,)
    ).fetchone()
    if row and row[0] == props["source_sha256"]:
        return
    if row and not force:
        raise ValueError("в БД другая граница; перезагрузка требует force=True")
    conn.execute(
        """
        INSERT INTO country_border (id, source, source_sha256, geom)
        VALUES (%s, %s, %s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))
        ON CONFLICT (id) DO UPDATE
        SET source = EXCLUDED.source,
            source_sha256 = EXCLUDED.source_sha256,
            geom = EXCLUDED.geom,
            loaded_at = now()
        """,
        (BORDER_ID, props["source"], props["source_sha256"], json.dumps(feature["geometry"])),
    )
    if row:
        conn.execute("UPDATE hotspots SET in_kz = NULL")


def mark_in_kz(conn: psycopg.Connection) -> int:
    """Считает in_kz для точек, где он ещё NULL. Возвращает число обновлённых строк."""
    cur = conn.execute(
        """
        UPDATE hotspots h
        SET in_kz = ST_Intersects(b.geom, h.geom)
        FROM country_border b
        WHERE b.id = %s AND h.in_kz IS NULL
        """,
        (BORDER_ID,),
    )
    return cur.rowcount
