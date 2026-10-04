"""Интервалы и согласие разметки."""

from collections.abc import Sequence


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Интервал Уилсона для доли k/n."""
    raise NotImplementedError


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    raise NotImplementedError


def bootstrap_ci(
    values: Sequence[float], stat, n_boot: int = 2000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    raise NotImplementedError
