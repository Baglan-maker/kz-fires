"""Оценка угрозы события для зоны подписчика: расстояние, ветер, тип земли."""

from dataclasses import dataclass, field
from typing import Literal

from kzfires.core.events import Event

WindClass = Literal["toward", "across", "away", "unknown"]
Level = Literal["L0", "L1", "L2"]


@dataclass(frozen=True)
class Zone:
    lat: float
    lon: float
    radius_km: float


@dataclass(frozen=True)
class Wind:
    speed_ms: float | None
    from_deg: float | None  # Open-Meteo wind_direction_10m: откуда дует


@dataclass(frozen=True)
class ThreatParams:
    toward_max_delta_deg: float = 45.0
    across_max_delta_deg: float = 135.0
    min_wind_speed_ms: float = 2.0


@dataclass(frozen=True)
class Threat:
    dist_km: float
    bearing_deg: float
    wind_class: WindClass
    land_cover: int | None
    level: Level
    reasons: dict = field(default_factory=dict)


def wind_to_deg(dir_from: float) -> float:
    """Куда дует ветер, по направлению «откуда»."""
    raise NotImplementedError


def assess(
    event: Event,
    zone: Zone,
    wind: Wind,
    land_cover: int | None,
    params: ThreatParams,
    in_mask: bool = False,
) -> Threat:
    raise NotImplementedError
