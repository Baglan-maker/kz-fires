"""Решение: отправить тревогу, обновление или промолчать (с причиной)."""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

from kzfires.core.events import Event
from kzfires.core.threat import Threat

Outcome = Literal["alert", "update", "suppress"]
SuppressReason = Literal[
    "in_mask",
    "out_of_radius",
    "below_threshold",
    "duplicate",
    "rate_limit",
    "quiet_hours",
    "too_old",
    "few_points",
]

LEVEL_RANK = {"L0": 0, "L1": 1, "L2": 2}


@dataclass(frozen=True)
class DecisionParams:
    min_points: int = 2
    max_age_h: float = 12.0
    max_messages_per_24h: int = 3
    min_gap_between_messages_h: float = 3.0


@dataclass(frozen=True)
class SentMessage:
    event_id: str
    sent_at: dt.datetime
    level: str
    n_points: int


@dataclass(frozen=True)
class Decision:
    outcome: Outcome
    reason: str
    data_age_min: float | None = None


def _suppress(reason: SuppressReason, age: float | None = None) -> Decision:
    return Decision("suppress", reason, age)


def decide(
    event: Event,
    threat: Threat,
    history: list[SentMessage],
    now: dt.datetime,
    params: DecisionParams,
) -> Decision:
    """`history` — сообщения этому подписчику по всем событиям."""
    if threat.reasons.get("in_mask"):
        return _suppress("in_mask")
    if threat.reasons.get("out_of_radius"):
        return _suppress("out_of_radius")
    if threat.level == "L0":
        return _suppress("below_threshold")

    age_min = round((now - event.last_acq_at).total_seconds() / 60, 1)
    if event.n_points < params.min_points:
        return _suppress("few_points", age_min)
    if age_min > params.max_age_h * 60:
        return _suppress("too_old", age_min)

    mine = sorted((m for m in history if m.event_id == event.id), key=lambda m: m.sent_at)
    outcome: Outcome = "alert"
    if mine:
        last = mine[-1]
        # сравниваем с максимумом уже отправленного, иначе L2 → L1 → L2 даст лишнее сообщение
        top = max(LEVEL_RANK[m.level] for m in mine)
        rose = LEVEL_RANK[threat.level] > top
        grew = event.n_points >= 2 * last.n_points
        waited = now - last.sent_at >= dt.timedelta(hours=params.min_gap_between_messages_h)
        if not ((rose or grew) and waited):
            return _suppress("duplicate", age_min)
        outcome = "update"

    day_ago = now - dt.timedelta(hours=24)
    if sum(m.sent_at > day_ago for m in history) >= params.max_messages_per_24h:
        return _suppress("rate_limit", age_min)
    return Decision(outcome, "level_up" if outcome == "update" else "new_event", age_min)
