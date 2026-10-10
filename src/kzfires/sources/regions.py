"""Области Казахстана из Natural Earth 10m admin-1."""

import urllib.request
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

NE_ADMIN1_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_1_states_provinces.zip"
ADMIN1_ZIP = Path("data/geo/ne_10m_admin_1_states_provinces.zip")


def load_regions(path: Path = ADMIN1_ZIP) -> gpd.GeoDataFrame:
    """Полигоны областей с кодом ISO 3166-2 (KZ-MAN, KZ-AKM, ...).

    Границы в NE 10m — до реформы 2022 года (нет Абайской, Жетысуской, Улытауской
    областей). Для западных и северных областей это не важно: реформа их не меняла.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(NE_ADMIN1_URL, path)
    g = gpd.read_file(f"zip://{path}")
    g = g[g["adm0_a3"] == "KAZ"]
    return g[["iso_3166_2", "name_en", "geometry"]].rename(columns={"iso_3166_2": "iso"})


def region_of(lat: np.ndarray, lon: np.ndarray, regions: gpd.GeoDataFrame) -> np.ndarray:
    """Код области для каждой точки; None, если точка не попала ни в одну."""
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon, lat), crs=4326)
    joined = gpd.sjoin(pts, regions.to_crs(4326), how="left", predicate="within")
    # точка на общей границе двух областей даёт две строки; берём первую
    joined = joined[~joined.index.duplicated(keep="first")]
    return joined["iso"].where(pd.notna(joined["iso"]), None).to_numpy(dtype=object)
