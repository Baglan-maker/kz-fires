"""Населённые пункты и промышленные объекты Казахстана из OpenStreetMap (Overpass API).

Данные OSM — ODbL: при публикации производных нужна атрибуция «© OpenStreetMap contributors».
"""

import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# без User-Agent Overpass отвечает 406
HEADERS = {"User-Agent": "kzfires/0.1 (wildfire research)", "Accept": "*/*"}
GEO_DIR = Path("data/geo")
SETTLEMENTS_PATH = GEO_DIR / "osm_settlements.geojson"
INDUSTRY_PATH = GEO_DIR / "osm_industry.geojson"

SETTLEMENT_TYPES = ("city", "town", "village")
KZ_AREA = 'area["ISO3166-1"="KZ"][admin_level=2]->.kz;'

SETTLEMENTS_QUERY = (
    f"[out:json][timeout:180];{KZ_AREA}"
    f'nwr(area.kz)[place~"^({"|".join(SETTLEMENT_TYPES)})$"];out center tags;'
)
# одним запросом Overpass не успевает (504), поэтому по частям. Скважины не берём:
# их десятки тысяч, а факелы стоят у установок подготовки, а не у каждой скважины
INDUSTRY_FILTERS = (
    "nwr(area.kz)[landuse=industrial];",
    'nwr(area.kz)[man_made~"^(works|flare|chimney|oil_gas_separator)$"];',
    "nwr(area.kz)[power=plant];",
)

# равнопромежуточная проекция с центром в Казахстане: расстояния до 5–30 км
# искажаются меньше чем на 1–2 %, этого хватает для порога «ближе 5 км»
KZ_AEQD = "+proj=aeqd +lat_0=48 +lon_0=68 +datum=WGS84 +units=m"


def _overpass(query: str, tries: int = 3) -> list[dict]:
    for attempt in range(tries):
        r = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=400)
        if r.status_code in (429, 504) and attempt < tries - 1:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        return r.json()["elements"]
    raise RuntimeError("Overpass не ответил")


def _to_gdf(elements: list[dict], kind_of) -> gpd.GeoDataFrame:
    rows = []
    for e in elements:
        c = e.get("center", e)
        if "lat" not in c:
            continue
        tags = e.get("tags", {})
        rows.append(
            {
                "osm_id": f"{e['type'][0]}{e['id']}",
                "name": tags.get("name:ru") or tags.get("name"),
                "kind": kind_of(tags),
                "lat": c["lat"],
                "lon": c["lon"],
            }
        )
    g = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy([r["lon"] for r in rows], [r["lat"] for r in rows]),
        crs=4326,
    )
    return g.drop(columns=["lat", "lon"])


def _industry_kind(tags: dict) -> str:
    for key in ("man_made", "power", "industrial", "landuse"):
        if key in tags:
            return f"{key}={tags[key]}"
    return "industrial"


def _load(path: Path, queries: list[str], kind_of, refresh: bool) -> gpd.GeoDataFrame:
    if path.exists() and not refresh:
        return gpd.read_file(path)
    elements = [e for q in queries for e in _overpass(q)]
    g = _to_gdf(elements, kind_of).drop_duplicates(subset="osm_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(g.to_json())
    return g


def load_settlements(refresh: bool = False) -> gpd.GeoDataFrame:
    return _load(SETTLEMENTS_PATH, [SETTLEMENTS_QUERY], lambda t: t.get("place"), refresh)


def load_industry(refresh: bool = False) -> gpd.GeoDataFrame:
    queries = [f"[out:json][timeout:300];{KZ_AREA}{f}out center tags;" for f in INDUSTRY_FILTERS]
    return _load(INDUSTRY_PATH, queries, _industry_kind, refresh)


class Nearest:
    """Ближайший объект к одной точке; проекция и индекс строятся один раз."""

    def __init__(self, targets: gpd.GeoDataFrame):
        self.targets = targets.to_crs(KZ_AEQD).reset_index(drop=True)
        self.sindex = self.targets.sindex

    def __call__(self, lat: float, lon: float, radius_km: float = 2.0) -> dict | None:
        if self.targets.empty:
            return None
        p = gpd.GeoSeries(gpd.points_from_xy([lon], [lat]), crs=4326).to_crs(KZ_AEQD).iloc[0]
        idx = int(self.sindex.nearest(p, return_all=False)[1][0])
        row = self.targets.iloc[idx]
        within = self.sindex.query(p.buffer(radius_km * 1000), predicate="intersects")
        name = row["name"]
        return {
            "km": round(row.geometry.distance(p) / 1000, 2),
            "kind": row["kind"],
            # пустое имя из GeoJSON приходит как NaN, а NaN ломает JSON для браузера
            "name": name if isinstance(name, str) else None,
            "count_within": int(len(within)),
            "radius_km": radius_km,
        }


def nearest(
    lat: np.ndarray, lon: np.ndarray, targets: gpd.GeoDataFrame
) -> tuple[np.ndarray, gpd.GeoDataFrame]:
    """Расстояние в км до ближайшего объекта и сам объект для каждой точки."""
    pts = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lon, lat), crs=4326).to_crs(KZ_AEQD)
    joined = gpd.sjoin_nearest(pts, targets.to_crs(KZ_AEQD), how="left", distance_col="dist_m")
    joined = joined[~joined.index.duplicated(keep="first")]
    return (joined["dist_m"] / 1000).to_numpy(), joined
