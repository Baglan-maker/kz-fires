"""Оценка угрозы события для зоны подписчика: расстояние, ветер, тип земли."""

from dataclasses import dataclass, field
from typing import Literal

from kzfires.core.events import Event
from kzfires.core.geo import angdiff, bearing_deg, haversine_km

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
    return (dir_from + 180) % 360


def nearest_point(event: Event, zone: Zone) -> tuple[float, float, float]:
    pts = event.points or [(event.centroid_lat, event.centroid_lon)]
    best = min(pts, key=lambda p: haversine_km(zone.lat, zone.lon, p[0], p[1]))
    return best[0], best[1], haversine_km(zone.lat, zone.lon, best[0], best[1])


def wind_class(wind: Wind, theta: float, params: ThreatParams) -> WindClass:
    if wind.speed_ms is None or wind.from_deg is None:
        return "unknown"
    # слабый ветер не задаёт направления: «в вашу сторону» не пишем
    if wind.speed_ms < params.min_wind_speed_ms:
        return "unknown"
    # ветер несёт дым от события к зоне, когда дует оттуда, где событие
    delta = angdiff(wind.from_deg, theta)
    if delta <= params.toward_max_delta_deg:
        return "toward"
    if delta <= params.across_max_delta_deg:
        return "across"
    return "away"


def assess(
    event: Event,
    zone: Zone,
    wind: Wind,
    land_cover: int | None,
    params: ThreatParams,
    in_mask: bool = False,
) -> Threat:
    lat, lon, dist = nearest_point(event, zone)
    theta = bearing_deg(zone.lat, zone.lon, lat, lon)
    wc = wind_class(wind, theta, params)
    reasons: dict = {}
    if in_mask:
        reasons["in_mask"] = True
    if dist > zone.radius_km:
        reasons["out_of_radius"] = True
    if reasons:
        level: Level = "L0"
    elif wc == "toward":
        level = "L2"
    else:
        level = "L1"
    return Threat(round(dist, 3), round(theta, 1), wc, land_cover, level, reasons)
