"""Оценка фильтра постоянных источников на размеченных точках.

Метки: 1 растительный пожар, 2 сельхозпал, 3 постоянный источник, 4 неясно.
Класс 4 в знаменатель не входит.
"""

import datetime as dt
import hashlib
import itertools
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

from kzfires.core.mask import MaskParams, build_mask
from kzfires.eval.stats import wilson
from kzfires.labeling import store

FIRE = {1, 2}
PERSISTENT = 3

GRID = {
    "cell_size_m": [500, 1000, 2000],
    "min_detections": [5],
    "min_months": [1, 3, 5],
    "min_years": [1, 2, 3],
    "buffer_cells": [0, 1],
}


def params_hash(params: MaskParams) -> str:
    blob = json.dumps(asdict(params), sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def load_labels(path: Path) -> pd.DataFrame:
    df = store.latest(store.read_labels(path))
    df = df[df["pass"] == "1"].copy()
    df["class"] = df["class"].astype(int)
    df["lat"] = df["lat"].astype(float)
    df["lon"] = df["lon"].astype(float)
    df["acq_at"] = pd.to_datetime(df["acq_at"], utc=True)
    return df.reset_index(drop=True)


def attach_firms(labels: pd.DataFrame, archive: pd.DataFrame) -> pd.DataFrame:
    """Подтягивает confidence и type из архива: нужны только для baseline."""
    a = archive[["satellite", "acq_at", "lat", "lon", "confidence", "fire_type"]].copy()
    a["lat_r"], a["lon_r"] = a["lat"].round(5), a["lon"].round(5)
    lab = labels.copy()
    lab["lat_r"], lab["lon_r"] = lab["lat"].round(5), lab["lon"].round(5)
    out = lab.merge(
        a.drop(columns=["lat", "lon"]),
        on=["satellite", "acq_at", "lat_r", "lon_r"],
        how="left",
        validate="one_to_one",
    )
    missing = out["confidence"].isna().sum()
    if missing:
        raise ValueError(f"{missing} размеченных точек не нашлись в архиве")
    return out.drop(columns=["lat_r", "lon_r"])


def _share(removed: np.ndarray, cls: np.ndarray, which) -> tuple[int, int]:
    sel = np.isin(cls, list(which))
    return int(removed[sel].sum()), int(sel.sum())


def score(removed: np.ndarray, cls: np.ndarray) -> dict:
    k_p, n_p = _share(removed, cls, {PERSISTENT})
    k_f, n_f = _share(removed, cls, FIRE)
    kept = ~removed & np.isin(cls, list(FIRE | {PERSISTENT}))
    k_prec, n_prec = int((kept & np.isin(cls, list(FIRE))).sum()), int(kept.sum())
    row = {
        "persistent_removed_k": k_p,
        "persistent_n": n_p,
        "fire_removed_k": k_f,
        "fire_n": n_f,
        "precision_k": k_prec,
        "precision_n": n_prec,
    }
    for name, k, n in (
        ("persistent_removed", k_p, n_p),
        ("fire_removed", k_f, n_f),
        ("precision", k_prec, n_prec),
    ):
        row[name] = k / n if n else float("nan")
        row[f"{name}_lo"], row[f"{name}_hi"] = wilson(k, n) if n else (np.nan, np.nan)
    return row


def baselines(labeled: pd.DataFrame) -> pd.DataFrame:
    cls = labeled["class"].to_numpy()
    rows = []
    for name, removed in (
        ("без фильтра", np.zeros(len(labeled), bool)),
        ("confidence = l", (labeled["confidence"] == "l").to_numpy()),
        ("FIRMS type = 2", (labeled["fire_type"] == 2).to_numpy()),
    ):
        rows.append({"variant": name, **score(removed, cls)})
    return pd.DataFrame(rows)


def grid_search(
    labeled: pd.DataFrame, snpp: pd.DataFrame, cutoff: dt.datetime, grid: dict = GRID
) -> pd.DataFrame:
    cls = labeled["class"].to_numpy()
    rows = []
    for values in itertools.product(*grid.values()):
        p = MaskParams(**dict(zip(grid.keys(), values, strict=True)))
        mask = build_mask(snpp, cutoff, p)
        removed = mask.contains_many(labeled["lat"], labeled["lon"])
        rows.append(
            {
                **asdict(p),
                "n_cells": len(mask),
                "params_hash": params_hash(p),
                **score(removed, cls),
            }
        )
    return pd.DataFrame(rows)


def choose(results: pd.DataFrame, max_fire_share: float = 0.05) -> pd.Series:
    """Максимум удалённых источников при ≤ 5 % удалённых пожаров.

    Правило записано до первого прогона: иначе порог подгоняется под метки.
    """
    n_fire = int(results["fire_n"].iloc[0])
    allowed = int(np.floor(max_fire_share * n_fire))
    ok = results[results["fire_removed_k"] <= allowed]
    order = ok.sort_values(
        [
            "persistent_removed_k",
            "fire_removed_k",
            "cell_size_m",
            "buffer_cells",
            "min_years",
            "min_months",
        ],
        ascending=[False, True, True, True, False, False],
        kind="stable",
    )
    return order.iloc[0]


def weighted_precision(
    labeled: pd.DataFrame,
    removed: np.ndarray,
    population: dict[str, int],
    n_boot: int = 2000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Precision после фильтра с весами страт по доле страты в генеральной совокупности.

    Выборка стратифицирована: запад и лес в ней перепредставлены. Поэтому
    precision каждой страты берётся с весом её доли во всех точках сезона
    (ratio-оценка). Интервал — bootstrap: внутри каждой страты ресэмплим
    все размеченные точки, а фильтр применяем уже к ресэмплу.
    """
    df = labeled.assign(removed=removed)
    df = df[df["class"].isin(FIRE | {PERSISTENT})]
    total = sum(population.values())
    groups = {s: g for s, g in df.groupby("stratum")}

    def est(parts: dict[str, pd.DataFrame]) -> float:
        num = den = 0.0
        for s, g in parts.items():
            w = population[s] / total
            kept = ~g["removed"].to_numpy()
            fire = g["class"].isin(FIRE).to_numpy()
            num += w * (kept & fire).mean()
            den += w * kept.mean()
        return num / den if den else float("nan")

    point = est(groups)
    rng = np.random.default_rng(seed)
    boots = [
        est({s: g.iloc[rng.integers(0, len(g), len(g))] for s, g in groups.items()})
        for _ in range(n_boot)
    ]
    lo, hi = np.nanquantile(boots, [0.025, 0.975])
    return point, float(lo), float(hi)
