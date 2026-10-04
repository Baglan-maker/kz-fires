"""Разовая проверка: ключ FIRMS, свежие точки по Казахстану, Абай 2023, ветер.

Запуск: python scripts/day0_check.py (ключ из FIRMS_MAP_KEY или .env).
Сырые CSV пишутся в data/day0/.
"""

from __future__ import annotations

import io
import math
import os
import sys
import time
from pathlib import Path

import pandas as pd
import requests

BASE = "https://firms.modaps.eosdis.nasa.gov"
OUT = Path("data/day0")

# west,south,east,north. Прямоугольники захватывают соседние страны, счётчики завышены.
KZ_BBOX = "46,40,88,56"
WEST_BBOX = "46.5,41,57,48"  # грубо Мангистау + Атырау
ABAI_BBOX = "79,49.5,81.5,51.5"  # окрестности Семей орманы
SEMEY = (50.41, 80.23)  # Семей: точка отсчёта расстояний и ветра


def load_key() -> str:
    key = os.environ.get("FIRMS_MAP_KEY", "").strip()
    env = Path(".env")
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FIRMS_MAP_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit("Нет ключа: задай FIRMS_MAP_KEY в окружении или в файле .env")
    return key


def redact(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def get(url: str, key: str, params: dict | None = None, tries: int = 3):
    """GET с повторами. URL и текст исключений с ключом не печатаем."""
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=90)
        except requests.RequestException as e:
            print(f"  сеть: {type(e).__name__}")
            time.sleep(2 * (i + 1))
            continue
        if r.status_code == 200:
            return r
        print(f"  HTTP {r.status_code}: {redact(r.text[:150], key)}")
        if r.status_code in (400, 401, 403, 404):
            return None  # повторять бессмысленно
        time.sleep(2 * (i + 1))
    return None


def fetch(key: str, source: str, bbox: str, days: int, date: str | None = None):
    """Точки FIRMS одним запросом: days от 1 до 5, date = первая дата окна."""
    url = f"{BASE}/api/area/csv/{key}/{source}/{bbox}/{days}"
    if date:
        url += f"/{date}"
    r = get(url, key)
    if r is None:
        return None
    text = r.text.strip()
    first = text.splitlines()[0] if text else ""
    if "latitude" not in first:
        print("  ответ не похож на CSV:", redact(text[:150], key))
        return None
    return pd.read_csv(io.StringIO(text))


def add_dt(df: pd.DataFrame) -> pd.DataFrame:
    """Склеивает acq_date и acq_time (HHMM, UTC) в одну колонку dt_utc."""
    df = df.copy()
    t = df["acq_time"].astype(int).astype(str).str.zfill(4)
    df["dt_utc"] = pd.to_datetime(
        df["acq_date"].astype(str) + " " + t, format="%Y-%m-%d %H%M", utc=True
    )
    return df


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def save(df: pd.DataFrame, slug: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / f"{slug}.csv", index=False)


def summarize(df: pd.DataFrame | None, title: str) -> None:
    if df is None:
        print(f"[{title}] нет данных")
        return
    print(f"[{title}] строк: {len(df)}; колонки: {list(df.columns)}")
    if df.empty:
        return
    df = add_dt(df)
    print("  по суткам:", df.groupby("acq_date").size().to_dict())
    for col in ("confidence", "daynight", "satellite", "instrument"):
        if col in df.columns:
            print(f"  {col}:", df[col].value_counts().to_dict())
    if "frp" in df.columns:
        print(f"  frp: медиана {df['frp'].median():.1f}, p95 {df['frp'].quantile(0.95):.1f}")
    last = df["dt_utc"].max()
    age = pd.Timestamp.now(tz="UTC") - last
    print(f"  последний пролёт: {last:%Y-%m-%d %H:%M} UTC, возраст от пролёта: {age}")


def kazakhstan(key: str) -> None:
    print("\n== 3. Свежие точки (NRT)")
    for src in ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"):
        df = fetch(key, src, KZ_BBOX, 1)
        summarize(df, f"{src}, bbox КЗ, 1 день")
        if df is not None:
            save(df, f"{src}_kz_1d")
        time.sleep(1)
    df = fetch(key, "VIIRS_SNPP_NRT", KZ_BBOX, 5)
    summarize(df, "VIIRS_SNPP_NRT, bbox КЗ, 5 дней")
    if df is not None:
        save(df, "VIIRS_SNPP_NRT_kz_5d")
    time.sleep(1)
    df = fetch(key, "VIIRS_SNPP_NRT", WEST_BBOX, 5)
    summarize(df, "VIIRS_SNPP_NRT, запад (грубый прямоугольник), 5 дней")
    if df is not None:
        save(df, "VIIRS_SNPP_NRT_west_5d")


def abai(key: str) -> None:
    print("\n== 4. Абай, 7-10 июня 2023 (архив, standard processing)")
    for source in ("VIIRS_SNPP_SP", "VIIRS_NOAA20_SP"):
        df = fetch(key, source, ABAI_BBOX, 4, "2023-06-07")
        if df is None or df.empty:
            print(f"[{source}] пусто или ошибка")
            continue
        df = add_dt(df)
        df["km_to_semey"] = [
            round(haversine_km(a, b, *SEMEY), 1)
            for a, b in zip(df["latitude"], df["longitude"], strict=True)
        ]
        save(df, f"abai_{source}")
        print(f"[{source}] строк: {len(df)}; по суткам: {df.groupby('acq_date').size().to_dict()}")
        per_hour = df.groupby(df["dt_utc"].dt.strftime("%d.%m %H:00")).size()
        print("  точек по часам пролёта (UTC):")
        print(per_hour.head(24).to_string())
        cols = ["acq_date", "acq_time", "latitude", "longitude", "confidence", "frp", "km_to_semey"]
        print("  первые точки окна:")
        print(df.sort_values("dt_utc")[cols].head(8).to_string(index=False))
        time.sleep(1)


def show_wind(title: str, r) -> None:
    if r is None:
        print(f"[{title}] нет ответа")
        return
    h = r.json()["hourly"]
    print(f"[{title}] время UTC; направление указывает, ОТКУДА дует, в градусах")
    rows = list(zip(h["time"], h["wind_speed_10m"], h["wind_direction_10m"], strict=True))
    for t, s, d in rows[::3]:
        print(f"  {t}  {str(s):>5} м/с  {str(d):>5}°")


def wind(key: str) -> None:
    print("\n== 5. Ветер (Open-Meteo, точка Семей)")
    lat, lon = SEMEY
    common = dict(
        latitude=lat,
        longitude=lon,
        hourly="wind_speed_10m,wind_direction_10m",
        wind_speed_unit="ms",
        timezone="UTC",
    )
    f = get("https://api.open-meteo.com/v1/forecast", key, params={**common, "forecast_days": 1})
    show_wind("прогноз, сегодня", f)
    a = get(
        "https://archive-api.open-meteo.com/v1/archive",
        key,
        params={**common, "start_date": "2023-06-08", "end_date": "2023-06-09"},
    )
    show_wind("архив, 8-9 июня 2023", a)


def main() -> None:
    key = load_key()
    print("== 1. Статус ключа")
    r = get(f"{BASE}/mapserver/mapkey_status/", key, params={"MAP_KEY": key})
    print(redact(r.text.strip(), key) if r else "  не удалось проверить ключ")
    print("\n== 2. Какие наборы и за какие даты доступны")
    r = get(f"{BASE}/api/data_availability/csv/{key}/ALL", key)
    print(redact(r.text.strip(), key) if r else "  не удалось получить")
    kazakhstan(key)
    abai(key)
    wind(key)
    print("\nГотово. Сырые CSV в data/day0/.")


if __name__ == "__main__":
    main()
