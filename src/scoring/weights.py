"""Step 5.1 — penalty/reward weight vectors."""
from __future__ import annotations

import numpy as np

from config.settings import settings

KEYS = ("supported", "unsupported", "contradicted", "hallucinated")


def weight_vector() -> np.ndarray:
    """Signed weights aligned with ``KEYS``: +W_s, -W_u, -W_c, -W_h."""
    w = settings.weights
    return np.array([w.supported, -w.unsupported, -w.contradicted, -w.hallucinated], dtype=float)


def base_score() -> float:
    return float(settings.weights.base)
