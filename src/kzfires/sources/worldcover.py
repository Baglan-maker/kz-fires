"""ESA WorldCover 2021 (10 м): какая земля вокруг точки."""

import csv
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import from_bounds

TILE_URL = (
    "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
    "ESA_WorldCover_10m_2021_v200_{tile}_Map.tif"
)
CLASSES = {
    10: "лес",
    20: "кустарник",
    30: "степь, травы",
    40: "пашня",
    50: "застройка",
    60: "голый грунт",
    70: "снег и лёд",
    80: "вода",
    90: "болото, тростник",
    95: "мангры",
    100: "мох и лишайник",
}
# пиксель VIIRS 375 м: класс в одной точке 10 м случаен, берём весь след пикселя
FOOTPRINT_M = 375.0
CACHE_PATH = Path("data/geo/worldcover_points.csv")

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_CACHEMAX": 512,
}


def tile_name(lat: float, lon: float) -> str:
    """Плитки 3×3°, имя по юго-западному углу: N51E069 покрывает 51–54° с. ш., 69–72° в. д."""
    a = math.floor(lat / 3) * 3
    b = math.floor(lon / 3) * 3
    ns = "N" if a >= 0 else "S"
    ew = "E" if b >= 0 else "W"
    return f"{ns}{abs(a):02d}{ew}{abs(b):03d}"


def _window_counts(ds, lat: float, lon: float, size_m: float) -> Counter:
    half_lat = size_m / 2 / 111_320
    half_lon = half_lat / math.cos(math.radians(lat))
    w = from_bounds(lon - half_lon, lat - half_lat, lon + half_lon, lat + half_lat, ds.transform)
    a = ds.read(1, window=w, boundless=True, fill_value=0)
    values, counts = np.unique(a[a > 0], return_counts=True)
    return Counter({int(k): int(v) for k, v in zip(values, counts, strict=True)})


def footprint_counts(lat: float, lon: float, size_m: float = FOOTPRINT_M) -> Counter:
    """Сколько пикселей каждого класса в квадрате size_m вокруг точки."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(TILE_URL.format(tile=tile_name(lat, lon))) as ds:
        return _window_counts(ds, lat, lon, size_m)


def majority(counts: Counter) -> int | None:
    return counts.most_common(1)[0][0] if counts else None


def _majority_for_tile(tile: str, pts: list[tuple[str, str, float, float]]) -> list[tuple]:
    out = []
    with rasterio.Env(**GDAL_ENV), rasterio.open(TILE_URL.format(tile=tile)) as ds:
        for lat_s, lon_s, lat, lon in pts:
            out.append((lat_s, lon_s, majority(_window_counts(ds, lat, lon, FOOTPRINT_M))))
    return out


def majority_classes(
    lat: pd.Series, lon: pd.Series, cache_path: Path = CACHE_PATH, workers: int = 16
) -> pd.Series:
    """Преобладающий класс в следе пикселя для каждой точки.

    lat и lon — строки из CSV FIRMS: по ним ключ кэша, чтобы повторный прогон
    не ходил в сеть. Одна и та же координата встречается много раз (факелы),
    поэтому запрашиваем только уникальные. Кэш дописывается по мере готовности
    кусков: если прогон оборвётся, сделанное не пропадёт.
    """
    keys = pd.DataFrame({"lat": lat.astype(str), "lon": lon.astype(str)})
    known = (
        set(zip(*_read_cache(cache_path)[["lat", "lon"]].to_numpy().T, strict=True))
        if cache_path.exists()
        else set()
    )
    todo = keys.drop_duplicates()
    todo = todo[[k not in known for k in zip(todo["lat"], todo["lon"], strict=True)]]

    if len(todo):
        by_tile: dict[str, list] = {}
        for lat_s, lon_s in zip(todo["lat"], todo["lon"], strict=True):
            la, lo = float(lat_s), float(lon_s)
            by_tile.setdefault(tile_name(la, lo), []).append((lat_s, lon_s, la, lo))
        # куски по 200 точек одной плитки: файл открывается один раз на кусок
        jobs = []
        for t, pts in by_tile.items():
            pts.sort()
            jobs += [(t, pts[i : i + 200]) for i in range(0, len(pts), 200)]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not cache_path.exists()
        with ThreadPoolExecutor(workers) as ex, cache_path.open("a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["lat", "lon", "worldcover"])
            for fut in as_completed([ex.submit(_majority_for_tile, *j) for j in jobs]):
                w.writerows(fut.result())
                f.flush()

    cache = _read_cache(cache_path).drop_duplicates(subset=["lat", "lon"])
    merged = keys.merge(cache, on=["lat", "lon"], how="left")
    return merged["worldcover"].astype("Int64").set_axis(lat.index)


def _read_cache(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"lat": str, "lon": str, "worldcover": "Int64"})
