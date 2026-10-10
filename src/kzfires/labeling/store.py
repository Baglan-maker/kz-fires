"""Файлы разметки: список точек к разметке и журнал меток.

Журнал только дописывается. Если человек исправил метку, в конце появляется
новая строка, а действующей считается последняя для пары (label_id, pass).
"""

import csv
import datetime as dt
import os
from pathlib import Path

import numpy as np
import pandas as pd

LABEL_COLUMNS = [
    "label_id",
    "satellite",
    "acq_at",
    "lat",
    "lon",
    "stratum",
    "set",
    "class",
    "label_confidence",
    "s2_pre_date",
    "s2_post_date",
    "cloud_pre",
    "cloud_post",
    "dnbr",
    "worldcover",
    "evidence",
    "labeled_at",
    "pass",
    "seconds_spent",
]
CLASSES = {1: "растительный пожар", 2: "сельхозпал", 3: "постоянный источник", 4: "неясно"}
CONFIDENCES = (1, 2, 3)

RELABEL_N = 60
RELABEL_MIN_DAYS = 7
RELABEL_SEED = 20261015


def read_todo(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def read_labels(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=LABEL_COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def latest(labels: pd.DataFrame) -> pd.DataFrame:
    return labels.drop_duplicates(subset=["label_id", "pass"], keep="last")


def relabel_ids(labels: pd.DataFrame, n: int = RELABEL_N) -> list[str]:
    """Первые n точек, размеченных в первом проходе (по времени первой метки)."""
    first = labels[labels["pass"] == "1"].drop_duplicates(subset="label_id", keep="first")
    return first.sort_values("labeled_at", kind="stable")["label_id"].head(n).tolist()


def queue(todo: pd.DataFrame, labels: pd.DataFrame, pass_no: int) -> list[str]:
    """Что осталось разметить в этом проходе, в порядке показа.

    Второй проход — те же 60 точек в новом случайном порядке: так прежняя
    последовательность не подсказывает прежние ответы.
    """
    done = set(labels.loc[labels["pass"] == str(pass_no), "label_id"])
    if pass_no == 1:
        ids = todo["label_id"].tolist()
    else:
        ids = relabel_ids(labels)
        ids = [ids[i] for i in np.random.default_rng(RELABEL_SEED).permutation(len(ids))]
    return [i for i in ids if i not in done]


def days_since_first_label(labels: pd.DataFrame, now: dt.datetime) -> float | None:
    first = labels.loc[labels["pass"] == "1", "labeled_at"]
    if first.empty:
        return None
    t0 = pd.to_datetime(first, utc=True).min()
    return (pd.Timestamp(now) - t0).total_seconds() / 86400


def make_row(point: dict, card: dict, form: dict, pass_no: int, now: dt.datetime) -> dict:
    cls = int(form["class"])
    conf = int(form["label_confidence"])
    if cls not in CLASSES:
        raise ValueError(f"класс должен быть 1–4, а не {cls}")
    if conf not in CONFIDENCES:
        raise ValueError(f"уверенность должна быть 1–3, а не {conf}")
    seconds = int(round(float(form.get("seconds_spent", 0))))
    if seconds < 0:
        raise ValueError("время не может быть отрицательным")
    pre = card.get("pre") or {}
    post = card.get("post") or {}
    return {
        "label_id": point["label_id"],
        "satellite": point["satellite"],
        "acq_at": point["acq_at"],
        "lat": point["lat"],
        "lon": point["lon"],
        "stratum": point.get("stratum", ""),
        "set": point["set"],
        "class": cls,
        "label_confidence": conf,
        "s2_pre_date": pre.get("date", ""),
        "s2_post_date": post.get("date", ""),
        "cloud_pre": pre.get("footprint_cloud", ""),
        "cloud_post": post.get("footprint_cloud", ""),
        "dnbr": "" if card.get("dnbr") is None else round(card["dnbr"], 4),
        "worldcover": card.get("worldcover", {}).get("majority") or "",
        "evidence": " ".join(str(form.get("evidence", "")).split()),
        "labeled_at": now.astimezone(dt.UTC).isoformat(timespec="seconds"),
        "pass": pass_no,
        "seconds_spent": seconds,
    }


def append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LABEL_COLUMNS)
        if new:
            w.writeheader()
        w.writerow(row)
        f.flush()
        # метка — часы работы человека: пусть ляжет на диск сразу
        os.fsync(f.fileno())
