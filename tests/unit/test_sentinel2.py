import datetime as dt
from types import SimpleNamespace

import numpy as np
import pytest

from kzfires.sources import sentinel2 as s2
from kzfires.sources.worldcover import majority, tile_name

ACQ = dt.datetime(2023, 6, 8, 6, 53, tzinfo=dt.UTC)


def scene(days: float, name: str | None = None) -> s2.Scene:
    return s2.Scene(
        id=name or f"S{days:+}",
        datetime=ACQ + dt.timedelta(days=days),
        scene_cloud=0.0,
        hrefs={},
        scale={},
        offset={},
    )


def test_pre_candidates_are_before_pass_and_nearest_first():
    scenes = [scene(d) for d in (-12, -10, -6, -2, -0.01, 0.01, 1)]
    got = [s.id for s in s2.pre_candidates(scenes, ACQ)]
    assert got == ["S-0.01", "S-2", "S-6", "S-10"]


def test_post_candidates_are_three_to_fifteen_days_after():
    scenes = [scene(d) for d in (-1, 2.9, 3, 8, 15, 15.1)]
    assert [s.id for s in s2.post_candidates(scenes, ACQ)] == ["S+3", "S+8", "S+15"]


def scl_with(footprint_value: int, rest: int = 4, px: int = s2.PX) -> np.ndarray:
    a = np.full((px, px), rest, dtype="uint8")
    fp = s2.footprint_slice(px)
    a[fp, fp] = footprint_value
    return a


def test_cloud_stats_counts_only_footprint_for_cloud_and_nodata():
    st = s2.cloud_stats(scl_with(9))
    assert st.footprint_cloud == 100.0 and st.footprint_nodata == 0.0
    assert 0 < st.window_cloud < 5
    assert s2.cloud_stats(scl_with(0)).footprint_nodata == 100.0


def test_pick_skips_cloudy_and_empty_scenes():
    cands = [scene(-1, "cloudy"), scene(-2, "empty"), scene(-3, "clear")]
    scls = {"cloudy": scl_with(8), "empty": scl_with(0), "clear": scl_with(4)}
    chosen, _, stats, usable = s2.pick(cands, lambda sc: scls[sc.id])
    assert chosen.id == "clear" and usable and stats.footprint_cloud == 0


def test_pick_falls_back_to_least_cloudy_and_flags_it():
    a, b = scl_with(9), scl_with(9)
    fp = s2.footprint_slice(s2.PX)
    b[fp, fp][: b[fp, fp].shape[0] // 2] = 4  # половина следа чистая
    cands = [scene(-1, "full"), scene(-2, "half")]
    chosen, _, stats, usable = s2.pick(cands, lambda sc: {"full": a, "half": b}[sc.id])
    assert chosen.id == "half" and not usable and stats.footprint_cloud == pytest.approx(50)


def test_pick_returns_none_without_data():
    chosen, scl, stats, usable = s2.pick([scene(-1)], lambda sc: scl_with(0, rest=0))
    assert chosen is None and scl is None and stats is None and not usable
    assert s2.pick([], lambda sc: None) == (None, None, None, False)


def test_nbr_guards_against_near_zero_denominator():
    nir = np.array([0.3, 0.004, 0.0], dtype="float32")
    swir = np.array([0.1, 0.004, 0.0], dtype="float32")
    out = s2.nbr(nir, swir)
    assert out[0] == pytest.approx(0.5)
    assert np.isnan(out[1]) and np.isnan(out[2])


def test_footprint_dnbr_uses_only_pixels_clear_on_both_dates():
    px = s2.PX
    pre = np.full((px, px), 0.5, dtype="float32")
    post = np.full((px, px), 0.1, dtype="float32")
    clear = np.ones((px, px), dtype=bool)
    assert s2.footprint_dnbr(pre, post, clear, clear) == pytest.approx(0.4)
    cloudy = np.zeros((px, px), dtype=bool)
    assert s2.footprint_dnbr(pre, post, clear, cloudy) is None


def fake_item(offset_applied: bool):
    band = SimpleNamespace(
        href="x", extra_fields={"raster:bands": [{"scale": 0.0001, "offset": -0.1}]}
    )
    return SimpleNamespace(
        id="S2B_TEST",
        datetime=ACQ,
        properties={"eo:cloud_cover": 3.0, "earthsearch:boa_offset_applied": offset_applied},
        assets={b: band for b in (*s2.BANDS, "scl")},
    )


def test_offset_is_not_subtracted_twice_when_catalog_applied_it():
    sc = s2.scene_from_item(fake_item(offset_applied=True))
    assert sc.offset["nir08"] == 0.0 and sc.scale["nir08"] == 0.0001
    raw = np.array([340, 0], dtype="uint16")  # вода и пропуск
    r = s2.reflectance(raw, sc.scale["nir08"], sc.offset["nir08"])
    assert r[0] == pytest.approx(0.034) and np.isnan(r[1])


def test_offset_is_applied_when_pixels_still_contain_it():
    sc = s2.scene_from_item(fake_item(offset_applied=False))
    assert sc.offset["swir22"] == -0.1
    assert sc.offset["visual"] == 0.0 and sc.scale["visual"] == 1.0


def test_png_bytes_is_png():
    rgb = s2.dnbr_colors(np.array([[0.4, -0.4], [0.0, np.nan]], dtype="float32"))
    assert rgb[1, 1].tolist() == [60, 60, 60]
    assert s2.png_bytes(rgb)[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.parametrize(
    "lat, lon, expected",
    [
        (50.55, 80.62, "N48E078"),
        (51.0, 69.0, "N51E069"),
        (53.99, 71.99, "N51E069"),
        (40.0, 46.5, "N39E045"),
    ],
)
def test_worldcover_tile_name(lat, lon, expected):
    assert tile_name(lat, lon) == expected


def test_worldcover_majority():
    from collections import Counter

    assert majority(Counter({30: 5, 40: 9})) == 40
    assert majority(Counter()) is None
