"""Тренировочный набор: на нём учатся различать классы до настоящей разметки.

Точки берутся из сезона 2022 (не DEV и не TEST) плюс первый очаг Абая 2023.
Абай исключается из рамки DEV, чтобы тренировка не подсказала ответ на настоящей точке.
Тренировочные метки ни в какие метрики не идут.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Geod

from kzfires.eval.sample import NORTH, SEED, TODO_COLUMNS, WEST, period_of, point_key, read_archive

PRACTICE_DIR = Path("data/labeling")
TODO_PATH = PRACTICE_DIR / "labels_todo_practice.csv"
YEAR = 2022
MIN_SPACING_KM = 20.0
CANDIDATES_PER_GROUP = 400

ABAI = {
    "satellite": "N",
    "acq_at": pd.Timestamp("2023-06-08 06:53", tz="UTC"),
    "lat": "50.55674",
    "lon": "80.62472",
}
ABAI_HINT = (
    "Известный большой лесной пожар у Семея, 8 июня 2023 года. Это заведомо класс 1. "
    "Посмотри, как свежая гарь выглядит на всех трёх картинках: тёмное пятно на обычном "
    "снимке, бурое на инфракрасном, красное на карте dNBR. Пожар шёл фронтом, поэтому "
    "пятно может быть сбоку от жёлтого квадрата."
)


@dataclass(frozen=True)
class Group:
    name: str
    n: int
    regions: frozenset[str] | None
    months: tuple[int, ...]
    fire_types: tuple[int, ...]
    worldcover: frozenset[int] | None
    hint: str


GROUPS = (
    Group(
        "flare",
        4,
        WEST,
        (4, 5, 6, 7, 8, 9, 10),
        (2,),
        None,
        "NASA пометило эту точку как статичный источник (type = 2): скорее всего, факел "
        "или промплощадка. На настоящей разметке этой подсказки не будет. Сравни «до» и "
        "«после»: меняется ли что-нибудь? Открой «спутник Google» и найди площадки и дороги.",
    ),
    Group(
        "field",
        4,
        NORTH,
        (8, 9, 10),
        (0,),
        frozenset({40}),
        "Пашня, конец лета или осень: частое время пала стерни. Ищи пятно с прямыми краями "
        "по границе поля. Если пятна нет или поле просто убрано (светлое) — так и отмечай.",
    ),
    Group(
        "steppe",
        3,
        NORTH | {"KZ-KAR", "KZ-AKT", "KZ-ZAP"},
        (4, 5, 6, 7, 8),
        (0,),
        frozenset({20, 30}),
        "Степь. Если здесь был пожар, края пятна неровные, как клякса, и пятно не повторяет "
        "границы полей.",
    ),
    Group(
        "forest",
        2,
        frozenset({"KZ-VOS", "KZ-PAV", "KZ-SEV"}),
        (4, 5, 6, 7, 8, 9, 10),
        (0,),
        frozenset({10}),
        "Лес. Гарь в лесу на инфракрасном снимке особенно тёмная, а dNBR большой.",
    ),
    Group(
        "reeds",
        2,
        frozenset({"KZ-ALA", "KZ-KZY"}),
        (4, 5, 6, 7, 8, 9, 10),
        (0, 3),
        frozenset({90}),
        "Тростник у воды (Балхаш, дельта Или, Сырдарья). Пожары тростника — обычный класс 1. "
        "Вода на снимках тёмная: не путай её с гарью, сравни «до» и «после».",
    ),
)

_GEOD = Geod(ellps="WGS84")


def _far_enough(lat: float, lon: float, chosen: list[tuple[float, float]]) -> bool:
    for la, lo in chosen:
        _, _, d = _GEOD.inv(lon, lat, lo, la)
        if d < MIN_SPACING_KM * 1000:
            return False
    return True


def _with(row: pd.Series, stratum: str, hint: str) -> pd.Series:
    out = row.copy()
    out["stratum"] = stratum
    out["hint"] = hint
    return out


def build(regions, worldcover_fn, seed: int = SEED) -> pd.DataFrame:
    from kzfires.sources.regions import region_of

    df = read_archive(YEAR)
    df = df[df["acq_at"].dt.month.map(period_of).notna()].reset_index(drop=True)
    df["region"] = region_of(df["lat_f"].to_numpy(), df["lon_f"].to_numpy(), regions)
    rng = np.random.default_rng(seed)

    rows = []
    chosen: list[tuple[float, float]] = []
    abai = read_archive(2023)
    abai = abai[abai["key"] == point_key(pd.DataFrame([ABAI]))[0]]
    if len(abai) != 1:
        raise RuntimeError("точка Абая не найдена в архиве 2023")
    rows.append(_with(abai.iloc[0], "abai", ABAI_HINT))
    chosen.append((abai["lat_f"].iloc[0], abai["lon_f"].iloc[0]))

    for g in GROUPS:
        cand = df[
            df["region"].isin(g.regions)
            & df["acq_at"].dt.month.isin(g.months)
            & df["fire_type"].isin(g.fire_types)
        ]
        cand = cand.iloc[rng.permutation(len(cand))].head(CANDIDATES_PER_GROUP)
        if g.worldcover is not None:
            wc = worldcover_fn(cand["lat"], cand["lon"])
            cand = cand[wc.isin(list(g.worldcover)).fillna(False).to_numpy()]
        taken = 0
        for _, r in cand.iterrows():
            if _far_enough(r["lat_f"], r["lon_f"], chosen):
                rows.append(_with(r, g.name, g.hint))
                chosen.append((r["lat_f"], r["lon_f"]))
                taken += 1
                if taken == g.n:
                    break

    out = pd.DataFrame(rows).reset_index(drop=True)
    out["label_id"] = [f"practice-{i:02d}" for i in range(1, len(out) + 1)]
    out["set"] = "practice"
    out["period"] = out["acq_at"].dt.month.map(period_of)
    keys = out["key"].tolist()
    out["acq_at"] = out["acq_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return out[[*TODO_COLUMNS, "hint"]].assign(key=keys)


def write(todo: pd.DataFrame, path: Path = TODO_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    todo.drop(columns=["key"]).to_csv(path, index=False)
    return path
