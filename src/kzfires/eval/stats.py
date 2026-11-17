"""Интервалы и согласие разметки."""

import math
from collections.abc import Sequence

import numpy as np


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Интервал Уилсона для доли k/n.

    Нормальное приближение p ± z·se при малом n и p около 0 или 1 вылезает
    за [0, 1] и занижает неопределённость; Уилсон так не делает.
    """
    if n <= 0:
        raise ValueError("n должно быть больше нуля")
    if not 0 <= k <= n:
        raise ValueError("k должно быть в пределах 0..n")
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def cohen_kappa(a: Sequence, b: Sequence) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("нужны две непустые последовательности одной длины")
    n = len(a)
    labels = sorted(set(a) | set(b), key=str)
    p_obs = sum(x == y for x, y in zip(a, b, strict=True)) / n
    p_exp = sum((list(a).count(c) / n) * (list(b).count(c) / n) for c in labels)
    if p_exp == 1.0:
        return 1.0
    return (p_obs - p_exp) / (1 - p_exp)


def bootstrap_ci(
    values: Sequence[float], stat, n_boot: int = 2000, seed: int = 0, alpha: float = 0.05
) -> tuple[float, float]:
    arr = np.asarray(values)
    if arr.size == 0:
        raise ValueError("пустая выборка")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_boot, arr.size))
    boots = np.array([stat(arr[i]) for i in idx])
    return float(np.quantile(boots, alpha / 2)), float(np.quantile(boots, 1 - alpha / 2))
