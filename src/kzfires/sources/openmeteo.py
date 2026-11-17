"""Ветер Open-Meteo: архив для ретро и прогноз для живого режима.

wind_direction_10m — направление, ОТКУДА дует. Скорость просим в м/с.
Ретро кэширует целый год по ячейке 0,25°: одна ячейка — один запрос.
"""

import datetime as dt
import math
import time
from pathlib import Path

import pandas as pd
import requests

from kzfires.core.threat import Wind

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CACHE_DIR = Path("data/weather")
CELL_DEG = 0.25
HOURLY = "wind_speed_10m,wind_direction_10m"


def cell_of(lat: float, lon: float) -> tuple[float, float]:
    """Центр ячейки 0,25°, в которую попадает точка."""
    return (
        (math.floor(lat / CELL_DEG) + 0.5) * CELL_DEG,
        (math.floor(lon / CELL_DEG) + 0.5) * CELL_DEG,
    )


def parse_hourly(data: dict) -> pd.DataFrame:
    h = data["hourly"]
    return pd.DataFrame(
        {
            "hour_utc": pd.to_datetime(h["time"], utc=True),
            "wind_speed": pd.to_numeric(h["wind_speed_10m"]),
            "wind_from_deg": pd.to_numeric(h["wind_direction_10m"]),
        }
    ).set_index("hour_utc")


def _get(url: str, params: dict, session, sleep) -> dict:
    for wait in (0, 5, 15, 60):
        if wait:
            sleep(wait)
        resp = session.get(url, params=params, timeout=60)
        if resp.status_code == 429 or resp.status_code >= 500:
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError(f"Open-Meteo: HTTP {resp.status_code}")


def year_series(
    lat: float, lon: float, year: int, *, session=None, sleep=time.sleep
) -> pd.DataFrame:
    clat, clon = cell_of(lat, lon)
    path = CACHE_DIR / f"{clat:.3f}_{clon:.3f}_{year}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    params = {
        "latitude": clat,
        "longitude": clon,
        "start_date": f"{year}-01-01",
        "end_date": f"{year}-12-31",
        "hourly": HOURLY,
        "wind_speed_unit": "ms",
        "timezone": "GMT",
    }
    df = parse_hourly(_get(ARCHIVE_URL, params, session or requests.Session(), sleep))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    return df


def wind_at(series: pd.DataFrame, at: dt.datetime) -> Wind:
    hour = pd.Timestamp(at).tz_convert("UTC").floor("h")
    if hour not in series.index:
        return Wind(None, None)
    row = series.loc[hour]
    speed = None if pd.isna(row["wind_speed"]) else float(row["wind_speed"])
    frm = None if pd.isna(row["wind_from_deg"]) else float(row["wind_from_deg"])
    return Wind(speed, frm)


def current_wind(lat: float, lon: float, *, session=None, sleep=time.sleep) -> Wind:
    params = {
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
        "current": HOURLY,
        "wind_speed_unit": "ms",
    }
    data = _get(FORECAST_URL, params, session or requests.Session(), sleep)
    cur = data.get("current", {})
    return Wind(cur.get("wind_speed_10m"), cur.get("wind_direction_10m"))
