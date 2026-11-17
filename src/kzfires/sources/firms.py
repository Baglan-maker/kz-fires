"""Клиент FIRMS: опрос area API и идемпотентная загрузка в hotspots.

Ключ FIRMS стоит в пути URL. Поэтому URL не попадает ни в логи, ни в тексты
ошибок: сетевые исключения requests содержат URL, их не пробрасываем,
а urllib3 на уровне DEBUG печатает путь запроса — его логгер приглушён.
"""

import datetime as dt
import io
import logging
import time
from pathlib import Path

import pandas as pd
import psycopg
import requests

from kzfires.config import FIRMS_BASE_URL, load_settings

logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger(__name__)

Bbox = tuple[float, float, float, float]  # west, south, east, north

RETRY_WAITS = (2, 4, 6)
TIMEOUT_S = 60

SATELLITE_CODES = {"SNPP": "N", "NOAA20": "N20", "NOAA21": "N21"}

DB_COLUMNS = [
    "product",
    "satellite",
    "acq_at",
    "lat_s",
    "lon_s",
    "confidence",
    "frp",
    "daynight",
    "bright_ti4",
    "bright_ti5",
    "scan",
    "track",
    "version",
    "fire_type",
]


class FirmsError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def source_info(source: str) -> tuple[str, str]:
    """VIIRS_NOAA20_NRT -> ("N20", "NRT")."""
    _, sat, product = source.split("_")
    return SATELLITE_CODES[sat], product


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def normalize(raw: pd.DataFrame, product: str) -> pd.DataFrame:
    """CSV FIRMS -> столбцы таблицы hotspots.

    lat_s и lon_s — строки из CSV как есть: уникальный ключ строится по ним,
    а круг через float мог бы дать другую пятую цифру.
    """
    if raw.empty:
        return pd.DataFrame(columns=DB_COLUMNS + ["lat", "lon"])
    t = raw["acq_time"].astype(str).str.zfill(4)
    df = pd.DataFrame(
        {
            "product": product,
            "satellite": raw["satellite"].astype(str),
            "acq_at": pd.to_datetime(
                raw["acq_date"].astype(str) + "T" + t.str[:2] + ":" + t.str[2:], utc=True
            ),
            "lat_s": raw["latitude"].astype(str),
            "lon_s": raw["longitude"].astype(str),
            "confidence": raw["confidence"].astype(str),
            "frp": pd.to_numeric(raw["frp"]),
            "daynight": raw["daynight"].astype(str),
            "bright_ti4": pd.to_numeric(raw["bright_ti4"]),
            "bright_ti5": pd.to_numeric(raw["bright_ti5"]),
            "scan": pd.to_numeric(raw["scan"]),
            "track": pd.to_numeric(raw["track"]),
            "version": raw["version"].astype(str),
            "fire_type": pd.to_numeric(raw["type"]) if "type" in raw else pd.NA,
        }
    )
    # NOAA20 в API приходит как "N20", в старых файлах встречается "1"
    df["satellite"] = df["satellite"].replace({"1": "N20", "N": "N"})
    df["lat"] = df["lat_s"].astype(float)
    df["lon"] = df["lon_s"].astype(float)
    return df


def _read_csv(text: str) -> pd.DataFrame:
    return pd.read_csv(
        io.StringIO(text), dtype={"latitude": str, "longitude": str, "acq_time": str}
    )


def read_country_csv(path: Path) -> pd.DataFrame:
    """Годовой файл FIRMS по стране (SP), уже в столбцах hotspots."""
    return normalize(_read_csv(path.read_text()), "SP")


def fetch_area(
    source: str,
    bbox: Bbox,
    days: int,
    date: dt.date | None = None,
    *,
    key: str | None = None,
    session=None,
    sleep=time.sleep,
) -> pd.DataFrame:
    """Точки из /api/area/csv за `days` суток (1..5), начиная с `date`.

    Ключ стоит в пути URL, поэтому URL не должен попадать в логи и тексты исключений.
    Ошибку FIRMS может вернуть текстом с кодом 200.
    """
    if not 1 <= days <= 5:
        raise ValueError("days должно быть от 1 до 5")
    key = key if key is not None else load_settings().firms_map_key
    if not key:
        raise FirmsError("FIRMS_MAP_KEY не задан в .env")
    session = session or requests.Session()
    _, product = source_info(source)
    area = ",".join(str(v) for v in bbox)
    url = f"{FIRMS_BASE_URL}/api/area/csv/{key}/{source}/{area}/{days}"
    if date is not None:
        url += f"/{date.isoformat()}"

    status: int | None = None
    for attempt in range(len(RETRY_WAITS) + 1):
        if attempt:
            sleep(RETRY_WAITS[attempt - 1])
        try:
            resp = session.get(url, timeout=TIMEOUT_S)
        except requests.RequestException as e:
            status = None
            last = f"{source}: сетевая ошибка {type(e).__name__}"
            log.warning(last)
            continue
        status = resp.status_code
        if 400 <= status < 500:
            # 4xx не лечится повтором: неверный ключ или запрос
            raise FirmsError(f"{source}: HTTP {status}", status)
        if status >= 500:
            last = f"{source}: HTTP {status}"
            log.warning(last)
            continue
        text = resp.text
        if not text.startswith("latitude"):
            raise FirmsError(f"{source}: ответ без данных: {_scrub(text[:120], key)!r}", status)
        return normalize(_read_csv(text), product)
    raise FirmsError(last, status) from None


def mapkey_status(*, key: str | None = None, session=None) -> dict:
    """Текущее число транзакций и лимит ключа."""
    key = key if key is not None else load_settings().firms_map_key
    session = session or requests.Session()
    try:
        resp = session.get(f"{FIRMS_BASE_URL}/mapserver/mapkey_status/?MAP_KEY={key}", timeout=30)
        data = resp.json()
    except (requests.RequestException, ValueError) as e:
        raise FirmsError(f"mapkey_status: {type(e).__name__}") from None
    return {k: data.get(k) for k in ("current_transactions", "transaction_limit")}


_COPY_COLS = (
    "product, satellite, acq_at, lat, lon, confidence, frp, daynight, "
    "bright_ti4, bright_ti5, scan, track, version, fire_type"
)


def load_hotspots(conn: psycopg.Connection, df: pd.DataFrame, product: str) -> int:
    """Вставляет точки и возвращает число реально вставленных строк.

    Повторная загрузка того же окна вставляет 0 строк. first_seen_at ставится
    только живым точкам (NRT) и только при настоящей вставке.
    """
    if df.empty:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TEMP TABLE IF NOT EXISTS _hs_in "
            "(LIKE hotspots INCLUDING DEFAULTS EXCLUDING GENERATED) ON COMMIT DROP"
        )
        cur.execute("TRUNCATE _hs_in")
        with cur.copy(f"COPY _hs_in ({_COPY_COLS}) FROM STDIN") as copy:
            for r in df.itertuples(index=False):
                copy.write_row(
                    (
                        product,
                        r.satellite,
                        r.acq_at.to_pydatetime(),
                        r.lat_s,
                        r.lon_s,
                        r.confidence if r.confidence in ("l", "n", "h") else None,
                        None if pd.isna(r.frp) else float(r.frp),
                        r.daynight,
                        None if pd.isna(r.bright_ti4) else float(r.bright_ti4),
                        None if pd.isna(r.bright_ti5) else float(r.bright_ti5),
                        None if pd.isna(r.scan) else float(r.scan),
                        None if pd.isna(r.track) else float(r.track),
                        r.version,
                        None if pd.isna(r.fire_type) else int(r.fire_type),
                    )
                )
        first_seen = "now()" if product == "NRT" else "NULL"
        cur.execute(
            f"""
            INSERT INTO hotspots ({_COPY_COLS}, first_seen_at)
            SELECT {_COPY_COLS}, {first_seen} FROM _hs_in
            ON CONFLICT (product, satellite, acq_at, lat, lon) DO NOTHING
            """
        )
        return cur.rowcount
