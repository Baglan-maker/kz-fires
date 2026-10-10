"""Стратифицированная выборка точек SP для разметки.

Рамка — все точки SP (SNPP и NOAA20) за сезон апрель–октябрь, до любых фильтров:
иначе нельзя измерить ни работу маски, ни ошибочно удалённые пожары.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

ARCHIVE_DIR = Path("data/archive")
ARCHIVE_FILES = {
    "N": "viirs-snpp_{year}_Kazakhstan.csv",
    "N20": "viirs-jpss1_{year}_Kazakhstan.csv",
}
LABELS_DIR = Path("labels")

SEED = 20261005
PERIODS = {"P1": (4, 5, 6), "P2": (7, 8), "P3": (9, 10)}
STRATA = ("S1", "S2", "S3", "S4", "S5", "S6")
DEV_QUOTAS = {"S1": 25, "S2": 20, "S3": 20, "S4": 15, "S5": 10, "S6": 10}

WEST = frozenset({"KZ-MAN", "KZ-ATY"})
NORTH = frozenset({"KZ-AKM", "KZ-KUS"})
STEPPE = frozenset({20, 30})
CROPLAND = 40
FOREST = 10
SETTLEMENT_KM = 5.0

# большой пожар не должен размножиться в выборке
CLUSTER_KM = 5.0
CLUSTER_H = 48.0
CLUSTER_MAX = 2

TODO_COLUMNS = ["label_id", "set", "stratum", "period", "satellite", "acq_at", "lat", "lon"]

_GEOD = Geod(ellps="WGS84")


def read_archive(year: int, archive_dir: Path = ARCHIVE_DIR) -> pd.DataFrame:
    """Годовые файлы FIRMS по стране. lat и lon остаются строками из CSV: это часть ключа точки."""
    frames = []
    for pattern in ARCHIVE_FILES.values():
        df = pd.read_csv(
            archive_dir / pattern.format(year=year),
            dtype={"latitude": str, "longitude": str, "acq_time": str},
        )
        frames.append(df)
    df = pd.concat(frames, ignore_index=True)
    hhmm = df["acq_time"].str.zfill(4)
    out = pd.DataFrame(
        {
            "satellite": df["satellite"].astype(str),
            "acq_at": pd.to_datetime(df["acq_date"] + " " + hhmm, format="%Y-%m-%d %H%M", utc=True),
            "lat": df["latitude"],
            "lon": df["longitude"],
            "fire_type": df["type"].astype("Int64"),
        }
    )
    out["lat_f"] = out["lat"].astype(float)
    out["lon_f"] = out["lon"].astype(float)
    out["key"] = point_key(out)
    return out


def point_key(df: pd.DataFrame) -> pd.Series:
    t = df["acq_at"].dt.strftime("%Y-%m-%dT%H:%MZ")
    return df["satellite"] + "|" + t + "|" + df["lat"] + "|" + df["lon"]


def period_of(month: int) -> str | None:
    for name, months in PERIODS.items():
        if month in months:
            return name
    return None


def assign_stratum(region: str | None, worldcover: int | None, settlement_km: float | None) -> str:
    """Первое подходящее правило в порядке S1…S6, поэтому страты не пересекаются."""
    if region in WEST:
        return "S1"
    if region in NORTH and worldcover in STEPPE:
        return "S2"
    if region in NORTH and worldcover == CROPLAND:
        return "S3"
    if worldcover == FOREST:
        return "S4"
    if settlement_km is not None and settlement_km <= SETTLEMENT_KM:
        return "S5"
    return "S6"


def split_quota(n: int, parts: int) -> list[int]:
    """25 на 3 периода -> [9, 8, 8]: остаток уходит в ранние периоды."""
    base, extra = divmod(n, parts)
    return [base + (1 if i < extra else 0) for i in range(parts)]


def build_frame(
    year: int, regions, settlements, worldcover_fn, archive_dir: Path = ARCHIVE_DIR
) -> pd.DataFrame:
    from kzfires.sources.osm import nearest
    from kzfires.sources.regions import region_of

    df = read_archive(year, archive_dir)
    df["period"] = df["acq_at"].dt.month.map(period_of)
    df = df[df["period"].notna()].reset_index(drop=True)
    df["region"] = region_of(df["lat_f"].to_numpy(), df["lon_f"].to_numpy(), regions)
    df["worldcover"] = worldcover_fn(df["lat"], df["lon"])
    df["settlement_km"], _ = nearest(df["lat_f"].to_numpy(), df["lon_f"].to_numpy(), settlements)
    df["stratum"] = [
        assign_stratum(r, None if pd.isna(w) else int(w), None if pd.isna(d) else float(d))
        for r, w, d in zip(df["region"], df["worldcover"], df["settlement_km"], strict=True)
    ]
    return df


@dataclass
class _Picker:
    """Помнит выбранные точки и не даёт взять третью в пределах 5 км и 48 часов."""

    lats: list[float]
    lons: list[float]
    times: list[float]

    def ok(self, lat: float, lon: float, t: float) -> bool:
        if not self.times:
            return True
        times = np.asarray(self.times)
        near_t = np.abs(times - t) <= CLUSTER_H * 3600
        if not near_t.any():
            return True
        k = int(near_t.sum())
        _, _, d = _GEOD.inv(
            np.full(k, lon),
            np.full(k, lat),
            np.asarray(self.lons)[near_t],
            np.asarray(self.lats)[near_t],
        )
        return int((np.asarray(d) <= CLUSTER_KM * 1000).sum()) < CLUSTER_MAX

    def take(self, cand: pd.DataFrame, n: int) -> list:
        taken = []
        if n <= 0:
            return taken
        secs = cand["acq_at"].astype("int64").to_numpy() / 1e9
        for idx, lat, lon, t in zip(cand.index, cand["lat_f"], cand["lon_f"], secs, strict=True):
            if self.ok(lat, lon, t):
                self.lats.append(lat)
                self.lons.append(lon)
                self.times.append(t)
                taken.append(idx)
                if len(taken) == n:
                    break
        return taken


def draw(
    frame: pd.DataFrame,
    quotas: dict[str, int],
    seed: int = SEED,
    exclude: frozenset[str] = frozenset(),
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Случайный отбор по стратам и периодам. Возвращает выборку и недобор по стратам.

    Рамка перемешивается один раз, дальше точки берутся по порядку: это то же самое,
    что случайный выбор внутри каждой ячейки «страта × период». Если в ячейке
    не хватило точек, недостающие добираются из других периодов той же страты.
    """
    rng = np.random.default_rng(seed)
    pool = frame[~frame["key"].isin(exclude)]
    pool = pool.iloc[rng.permutation(len(pool))]
    picker = _Picker([], [], [])
    chosen: list = []
    shortfall: dict[str, int] = {}
    for stratum, quota in quotas.items():
        cand_s = pool[pool["stratum"] == stratum]
        taken: list = []
        for period, q in zip(PERIODS, split_quota(quota, len(PERIODS)), strict=True):
            taken += picker.take(cand_s[cand_s["period"] == period], q)
        if len(taken) < quota:
            taken += picker.take(cand_s[~cand_s.index.isin(taken)], quota - len(taken))
        if len(taken) < quota:
            shortfall[stratum] = quota - len(taken)
        chosen += taken
    selected = pool.loc[chosen]
    # порядок показа случайный: первые 60 меток — случайное подмножество для перемаркировки
    selected = selected.iloc[rng.permutation(len(selected))]
    return selected, shortfall


def to_todo(selected: pd.DataFrame, set_name: str) -> pd.DataFrame:
    todo = selected.copy()
    todo["label_id"] = [f"{set_name}-{i:03d}" for i in range(1, len(todo) + 1)]
    todo["set"] = set_name
    todo["acq_at"] = todo["acq_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return todo[TODO_COLUMNS].reset_index(drop=True)


def _git_sha() -> str | None:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def write_sample(
    todo: pd.DataFrame,
    frame: pd.DataFrame,
    shortfall: dict[str, int],
    set_name: str,
    year: int,
    quotas: dict[str, int],
    seed: int,
    n_excluded: int,
    out_dir: Path = LABELS_DIR,
) -> Path:
    """labels_todo_<set>.csv и рядом .meta.json с размерами страт для взвешивания."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"labels_todo_{set_name}.csv"
    todo.to_csv(path, index=False)
    meta = {
        "set": set_name,
        "year": year,
        "seed": seed,
        "quotas": quotas,
        "periods": {k: list(v) for k, v in PERIODS.items()},
        "frame_size": int(len(frame)),
        "excluded_practice_points": n_excluded,
        "population": {s: int((frame["stratum"] == s).sum()) for s in STRATA},
        "population_by_period": {
            f"{s}/{p}": int(((frame["stratum"] == s) & (frame["period"] == p)).sum())
            for s in STRATA
            for p in PERIODS
        },
        "shortfall": shortfall,
        "rules": {
            "cluster_km": CLUSTER_KM,
            "cluster_h": CLUSTER_H,
            "cluster_max": CLUSTER_MAX,
            "settlement_km": SETTLEMENT_KM,
            "west": sorted(WEST),
            "north": sorted(NORTH),
        },
        "git_sha": _git_sha(),
        "created_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return path
