"""Годовые файлы FIRMS SP по Казахстану из data/archive/.

FIRMS режет страну той же границей Natural Earth 10m, что и мы, поэтому все
строки архива лежат в Казахстане и in_kz для них не пересчитываем.
"""

from pathlib import Path

import pandas as pd

from kzfires.sources.firms import read_country_csv

ARCHIVE_DIR = Path("data/archive")
CACHE_DIR = ARCHIVE_DIR / "cache"
FILE_PREFIX = {"N": "viirs-snpp", "N20": "viirs-jpss1"}
COLUMNS = ["satellite", "acq_at", "lat", "lon", "confidence", "frp", "daynight", "fire_type"]


def path_for(satellite: str, year: int) -> Path:
    prefix = FILE_PREFIX[satellite]
    return ARCHIVE_DIR / f"{prefix}_{year}_Kazakhstan.csv"


def read_year(satellite: str, year: int) -> pd.DataFrame:
    src = path_for(satellite, year)
    cached = CACHE_DIR / f"{src.stem}.pkl"
    if cached.exists() and cached.stat().st_mtime >= src.stat().st_mtime:
        return pd.read_pickle(cached)
    df = read_country_csv(src)[COLUMNS].reset_index(drop=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_pickle(cached)
    return df


def load(years: range | list[int], satellites: tuple[str, ...] = ("N", "N20")) -> pd.DataFrame:
    parts = [
        read_year(s, y)
        for y in years
        for s in satellites
        # NOAA20 летает с 2018 года
        if path_for(s, y).exists()
    ]
    df = pd.concat(parts, ignore_index=True)
    return df.sort_values(["acq_at", "satellite", "lat", "lon"], kind="stable").reset_index(
        drop=True
    )
