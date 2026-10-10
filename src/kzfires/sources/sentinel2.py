"""Снимки Sentinel-2 L2A из каталога Earth Search: окно вокруг точки до и после пролёта."""

import datetime as dt
import warnings
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.errors import NotGeoreferencedWarning
from rasterio.io import MemoryFile
from rasterio.windows import from_bounds

STAC_URL = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
# готовая цветная картинка TCI (один файл вместо трёх) и каналы для NBR по 20 м:
# плитки 20-метровых файлов в разы легче, а скачивание здесь самое долгое
BANDS = ("visual", "nir08", "swir22")
# тень облака, облака средней и высокой вероятности, перистые
SCL_CLOUD = (3, 8, 9, 10)
SCL_NODATA = 0

WINDOW_M = 3000
PX = 300
FOOTPRINT_M = 375

PRE_DAYS = 10
POST_DAYS = (3, 15)
MAX_CLOUD_PCT = 20.0
MAX_NODATA_PCT = 10.0
MAX_TRIES = 4

GDAL_ENV = {
    "AWS_NO_SIGN_REQUEST": "YES",
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MULTIPLEX": "YES",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
}


@dataclass(frozen=True)
class Scene:
    id: str
    datetime: dt.datetime
    scene_cloud: float | None
    hrefs: dict[str, str]
    scale: dict[str, float]
    offset: dict[str, float]


@dataclass(frozen=True)
class CloudStats:
    footprint_cloud: float
    footprint_nodata: float
    window_cloud: float


def scene_from_item(item) -> Scene:
    hrefs, scale, offset = {}, {}, {}
    # Earth Search уже вычел сдвиг базовой линии 04.00 из самих пикселей
    # (вода на Балхаше: NIR около 340, а не 1000+), хотя в raster:bands всё ещё пишет −0,1.
    # Вычесть его второй раз — получить отрицательную яркость и dNBR в тысячи.
    offset_in_pixels = bool(item.properties.get("earthsearch:boa_offset_applied"))
    for band in (*BANDS, "scl"):
        asset = item.assets[band]
        hrefs[band] = asset.href
        rb = (asset.extra_fields.get("raster:bands") or [{}])[0]
        scale[band] = rb.get("scale", 1.0) if band != "visual" else 1.0
        offset[band] = 0.0 if offset_in_pixels or band == "visual" else rb.get("offset", 0.0)
    return Scene(
        id=item.id,
        datetime=item.datetime,
        scene_cloud=item.properties.get("eo:cloud_cover"),
        hrefs=hrefs,
        scale=scale,
        offset=offset,
    )


def open_client():
    from pystac_client import Client

    return Client.open(STAC_URL)


def search(client, lat: float, lon: float, start: dt.datetime, end: dt.datetime) -> list[Scene]:
    items = client.search(
        collections=[COLLECTION],
        intersects={"type": "Point", "coordinates": [lon, lat]},
        datetime=f"{start:%Y-%m-%dT%H:%M:%S}Z/{end:%Y-%m-%dT%H:%M:%S}Z",
        query={"eo:cloud_cover": {"lt": 80}},
        max_items=40,
    ).items()
    return sorted((scene_from_item(i) for i in items), key=lambda s: s.datetime)


def pre_candidates(scenes: list[Scene], acq_at: dt.datetime) -> list[Scene]:
    """Снимки строго до пролёта, не раньше чем за 10 суток; ближайший первым."""
    lo = acq_at - dt.timedelta(days=PRE_DAYS)
    return sorted(
        (s for s in scenes if lo <= s.datetime < acq_at), key=lambda s: s.datetime, reverse=True
    )


def post_candidates(scenes: list[Scene], acq_at: dt.datetime) -> list[Scene]:
    """Снимки через 3–15 суток после пролёта; ближайший первым: гарь со временем зарастает."""
    lo = acq_at + dt.timedelta(days=POST_DAYS[0])
    hi = acq_at + dt.timedelta(days=POST_DAYS[1])
    return sorted((s for s in scenes if lo <= s.datetime <= hi), key=lambda s: s.datetime)


@lru_cache(maxsize=32)
def _to_crs(crs_wkt: str) -> Transformer:
    return Transformer.from_crs(4326, crs_wkt, always_xy=True)


def read_band(href: str, lat: float, lon: float, resampling=Resampling.nearest) -> np.ndarray:
    """Окно WINDOW_M вокруг точки, PX×PX. Одноканальный файл — 2D, TCI — PX×PX×3."""
    with rasterio.Env(**GDAL_ENV), rasterio.open(href) as ds:
        x, y = _to_crs(ds.crs.to_wkt()).transform(lon, lat)
        h = WINDOW_M / 2
        w = from_bounds(x - h, y - h, x + h, y + h, ds.transform)
        a = ds.read(
            window=w,
            out_shape=(ds.count, PX, PX),
            resampling=resampling,
            boundless=True,
            fill_value=0,
        )
    return a[0] if a.shape[0] == 1 else np.moveaxis(a, 0, 2)


def footprint_slice(px: int = PX) -> slice:
    half = max(1, round(px * FOOTPRINT_M / WINDOW_M / 2))
    c = px // 2
    return slice(c - half, c + half)


def cloud_stats(scl: np.ndarray) -> CloudStats:
    fp = footprint_slice(scl.shape[0])
    f = scl[fp, fp]
    valid = scl != SCL_NODATA
    return CloudStats(
        footprint_cloud=float(np.isin(f, SCL_CLOUD).mean() * 100),
        footprint_nodata=float((f == SCL_NODATA).mean() * 100),
        window_cloud=float(np.isin(scl[valid], SCL_CLOUD).mean() * 100) if valid.any() else 100.0,
    )


def pick(
    candidates: list[Scene], read_scl, max_tries: int = MAX_TRIES
) -> tuple[Scene | None, np.ndarray | None, CloudStats | None, bool]:
    """Первый по порядку снимок без облаков и пропусков над точкой.

    Если такого нет среди первых max_tries, возвращает наименее облачный из просмотренных
    и флаг usable=False: пусть человек сам решит, видно ли что-нибудь.
    """
    head = candidates[:max_tries]
    # маски облаков читаем все сразу: по одной выходит до четырёх сетевых пауз подряд
    with ThreadPoolExecutor(max(1, len(head))) as ex:
        scls = list(ex.map(read_scl, head))
    tried = []
    for scene, scl in zip(head, scls, strict=True):
        st = cloud_stats(scl)
        if st.footprint_nodata <= MAX_NODATA_PCT and st.footprint_cloud <= MAX_CLOUD_PCT:
            return scene, scl, st, True
        tried.append((scene, scl, st))
    with_data = [t for t in tried if t[2].footprint_nodata <= MAX_NODATA_PCT]
    if not with_data:
        return None, None, None, False
    scene, scl, st = min(with_data, key=lambda t: t[2].footprint_cloud)
    return scene, scl, st, False


def reflectance(raw: np.ndarray, scale: float, offset: float) -> np.ndarray:
    r = raw.astype("float32") * scale + offset
    r[raw == 0] = np.nan
    return r


def nbr(nir: np.ndarray, swir: np.ndarray) -> np.ndarray:
    """Normalized Burn Ratio: живая растительность — высокий, гарь — низкий."""
    total = nir + swir
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (nir - swir) / total
    # почти чёрный пиксель (тень, вода) даёт деление на ноль и NBR в сотни
    out[~(total > 0.01)] = np.nan
    return out


def clear_mask(scl: np.ndarray) -> np.ndarray:
    return (scl != SCL_NODATA) & ~np.isin(scl, SCL_CLOUD)


def footprint_dnbr(pre_nbr, post_nbr, pre_clear, post_clear) -> float | None:
    """dNBR = NBR(до) − NBR(после), среднее по следу пикселя VIIRS там, где оба снимка чистые."""
    fp = footprint_slice(pre_nbr.shape[0])
    d = (pre_nbr - post_nbr)[fp, fp]
    ok = pre_clear[fp, fp] & post_clear[fp, fp] & np.isfinite(d)
    return float(d[ok].mean()) if ok.any() else None


def joint_limits(arrays: list[np.ndarray], lo_pct: float = 2, hi_pct: float = 98) -> tuple:
    """Общая растяжка яркости для «до» и «после»: иначе тёмная гарь вытянется в светлое."""
    vals = np.concatenate([a[np.isfinite(a)].ravel() for a in arrays if a is not None])
    if vals.size == 0:
        return 0.0, 1.0
    lo, hi = np.percentile(vals, [lo_pct, hi_pct])
    return float(lo), float(max(hi, lo + 1e-6))


def to_uint8(rgb: np.ndarray, lo: float, hi: float) -> np.ndarray:
    x = np.clip((rgb - lo) / (hi - lo), 0, 1)
    x = np.nan_to_num(x, nan=0.0)
    return (x * 255).astype("uint8")


def dnbr_colors(d: np.ndarray, limit: float = 0.5) -> np.ndarray:
    """Без порогов: серое — без изменений, красное — потемнело, зелёное — позеленело."""
    t = np.clip(np.nan_to_num(d, nan=0.0) / limit, -1, 1)
    gray = np.array([200, 200, 200], dtype="float32")
    red = np.array([200, 20, 20], dtype="float32")
    green = np.array([30, 150, 60], dtype="float32")
    pos = np.clip(t, 0, 1)[..., None]
    neg = np.clip(-t, 0, 1)[..., None]
    rgb = gray + (red - gray) * pos + (green - gray) * neg
    rgb[~np.isfinite(d)] = (60, 60, 60)
    return rgb.astype("uint8")


def png_bytes(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    # PNG для браузера, координаты ему не нужны
    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
    with MemoryFile() as mem:
        with mem.open(driver="PNG", width=w, height=h, count=3, dtype="uint8") as dst:
            dst.write(np.moveaxis(rgb, 2, 0))
        return mem.read()
