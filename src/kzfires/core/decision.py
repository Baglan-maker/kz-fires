"""Решение: отправить тревогу, обновление или промолчать (с причиной)."""

import datetime as dt
from dataclasses import dataclass
from typing import Literal

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


def decide(
    threat: Threat,
    history: list[SentMessage],
    now: dt.datetime,
    params: DecisionParams,
) -> Decision:
    raise NotImplementedError
