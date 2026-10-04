"""Сверка решений конвейера с замороженными метками, метрики с интервалами."""

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalResult:
    metric: str
    stratum: str
    n: int
    k: int
    value: float
    ci_low: float
    ci_high: float
    method: str


def run_eval(
    pipeline_run_id: int, label_set_id: int, expected_params_hash: str
) -> list[EvalResult]:
    """Отказывается считать, если params_hash прогона не совпадает с замороженным."""
    raise NotImplementedError
