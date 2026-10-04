"""Граница Казахстана из Natural Earth 10m admin-0: KAZ плюс арендованный Байконур (KAB).

Годовые файлы FIRMS по стране нарезаны этой же границей (вне неё 0 строк архива
2012–2024), поэтому живые NRT-точки режем ею же: ретро и живой режим должны считать
«Казахстан» одинаково. Natural Earth — public domain.

Запуск: python scripts/build_border.py
"""

import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd

NE_URL = "https://naciscdn.org/naturalearth/10m/cultural/ne_10m_admin_0_countries.zip"
OUT_DIR = Path("data/border")
ZIP_PATH = OUT_DIR / "ne_10m_admin_0_countries.zip"
OUT_PATH = OUT_DIR / "kz_ne10m.geojson"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not ZIP_PATH.exists():
        urllib.request.urlretrieve(NE_URL, ZIP_PATH)
    sha = hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest()
    with zipfile.ZipFile(ZIP_PATH) as zf:
        version = zf.read("ne_10m_admin_0_countries.VERSION.txt").decode().strip()

    ne = gpd.read_file(f"zip://{ZIP_PATH}")
    parts = ne[ne["SOVEREIGNT"] == "Kazakhstan"]
    geom = parts.union_all()
    area_km2 = gpd.GeoSeries([geom], crs=4326).to_crs(6933).area.iloc[0] / 1e6

    feature = {
        "type": "Feature",
        "properties": {
            "name": "Kazakhstan",
            "parts": sorted(parts["ADM0_A3"]),
            "source": f"Natural Earth 10m admin-0 countries v{version}",
            "source_sha256": sha,
            "license": "public domain",
        },
        "geometry": json.loads(gpd.GeoSeries([geom]).to_json())["features"][0]["geometry"],
    }
    OUT_PATH.write_text(json.dumps({"type": "FeatureCollection", "features": [feature]}))
    print(f"{OUT_PATH}: {sorted(parts['ADM0_A3'])}, NE v{version}, {area_km2:,.0f} км²")


if __name__ == "__main__":
    main()
