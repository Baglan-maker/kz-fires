"""Карточка точки для разметки: снимки до и после, dNBR, тип земли, объекты рядом.

Сюда не попадает ничего из FIRMS, кроме места и времени: ни confidence, ни FRP,
ни type, ни соседние детекции. Иначе метка повторит сигнал, по которому
построена маска, и оценка маски завысится.
"""

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from rasterio.enums import Resampling

from kzfires.sources import sentinel2 as s2
from kzfires.sources import worldcover as wc

IMAGES = ("pre_rgb", "post_rgb", "pre_swir", "post_swir", "dnbr")


def _parse_time(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def _scene_info(scene: s2.Scene | None, stats: s2.CloudStats | None, usable: bool) -> dict | None:
    if scene is None:
        return None
    return {
        "id": scene.id,
        "date": scene.datetime.date().isoformat(),
        "datetime": scene.datetime.isoformat(timespec="minutes"),
        "footprint_cloud": round(stats.footprint_cloud, 1),
        "window_cloud": round(stats.window_cloud, 1),
        "clear": usable,
    }


def _landcover(lat: float, lon: float) -> dict:
    counts = wc.footprint_counts(lat, lon)
    total = sum(counts.values()) or 1
    shares = [
        {"code": k, "name": wc.CLASSES.get(k, str(k)), "pct": round(v * 100 / total)}
        for k, v in counts.most_common()
        if v * 100 / total >= 3
    ]
    return {"majority": wc.majority(counts), "shares": shares}


def _links(lat: float, lon: float) -> dict:
    return {
        "osm": f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=14/{lat}/{lon}",
        "google": f"https://www.google.com/maps/@{lat},{lon},2500m/data=!3m1!1e3",
    }


def build(point: dict, out_dir: Path, client, settlements, industry) -> dict:
    """Собирает карточку и пишет её в out_dir/<label_id>/: card.json и пять PNG."""
    lat, lon = float(point["lat"]), float(point["lon"])
    acq_at = _parse_time(point["acq_at"])
    card: dict = {"label_id": point["label_id"], "links": _links(lat, lon)}

    with ThreadPoolExecutor(12) as pool:
        lc = pool.submit(_landcover, lat, lon)
        # один запрос к каталогу на весь интервал вместо двух
        scenes = s2.search(
            client,
            lat,
            lon,
            acq_at - dt.timedelta(days=s2.PRE_DAYS),
            acq_at + dt.timedelta(days=s2.POST_DAYS[1]),
        )

        def read_scl(scene):
            return s2.read_band(scene.hrefs["scl"], lat, lon)

        f_picks = {
            "pre": pool.submit(s2.pick, s2.pre_candidates(scenes, acq_at), read_scl),
            "post": pool.submit(s2.pick, s2.post_candidates(scenes, acq_at), read_scl),
        }
        picks = {tag: f.result() for tag, f in f_picks.items()}

        def read(job):
            tag, band = job
            scene = picks[tag][0]
            if band == "visual":
                return tag, band, s2.read_band(scene.hrefs[band], lat, lon)
            raw = s2.read_band(scene.hrefs[band], lat, lon, resampling=Resampling.bilinear)
            return tag, band, s2.reflectance(raw, scene.scale[band], scene.offset[band])

        jobs = [(t, b) for t, p in picks.items() if p[0] is not None for b in s2.BANDS]
        bands: dict[str, dict] = {}
        for tag, band, arr in pool.map(read, jobs):
            bands.setdefault(tag, {})[band] = arr
        card["worldcover"] = lc.result()

    for tag, (scene, _, stats, usable) in picks.items():
        card[tag] = _scene_info(scene, stats, usable)

    pre, post = bands.get("pre"), bands.get("post")
    images: dict[str, np.ndarray] = {}
    # TCI уже растянут ESA одинаково для всех снимков, поэтому «до» и «после» сравнимы как есть
    for t, b in bands.items():
        images[f"{t}_rgb"] = b["visual"]
    # SWIR-композит: каждый канал растягиваем общей шкалой для обеих дат
    channels = [
        {t: b["swir22"] for t, b in bands.items()},
        {t: b["nir08"] for t, b in bands.items()},
        {
            t: np.where(b["visual"][..., 0] > 0, b["visual"][..., 0] / 255, np.nan)
            for t, b in bands.items()
        },
    ]
    stretched = []
    for ch in channels:
        lo, hi = s2.joint_limits(list(ch.values()))
        stretched.append({t: s2.to_uint8(a, lo, hi) for t, a in ch.items()})
    for t in bands:
        images[f"{t}_swir"] = np.dstack([c[t] for c in stretched])

    card["dnbr"] = None
    if pre is not None and post is not None:
        pre_nbr, post_nbr = (
            s2.nbr(pre["nir08"], pre["swir22"]),
            s2.nbr(post["nir08"], post["swir22"]),
        )
        pre_ok, post_ok = s2.clear_mask(picks["pre"][1]), s2.clear_mask(picks["post"][1])
        d = pre_nbr - post_nbr
        d[~(pre_ok & post_ok)] = np.nan
        images["dnbr"] = s2.dnbr_colors(d)
        card["dnbr"] = s2.footprint_dnbr(pre_nbr, post_nbr, pre_ok, post_ok)

    if settlements is not None:
        card["settlement"] = settlements(lat, lon, radius_km=5.0)
    if industry is not None:
        card["industry"] = industry(lat, lon, radius_km=2.0)

    folder = out_dir / point["label_id"]
    folder.mkdir(parents=True, exist_ok=True)
    for name, arr in images.items():
        (folder / f"{name}.png").write_bytes(s2.png_bytes(arr))
    card["images"] = sorted(images)
    card["window_m"] = s2.WINDOW_M
    card["footprint_m"] = s2.FOOTPRINT_M
    (folder / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=1))
    return card


def load_cached(out_dir: Path, label_id: str) -> dict | None:
    p = out_dir / label_id / "card.json"
    return json.loads(p.read_text()) if p.exists() else None
