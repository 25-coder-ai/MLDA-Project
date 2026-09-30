"""Steps 5.1–5.3 — trust-score calculation, hard failure gates and verdict routing.

    S = clamp(0, 100, S_base + W_s·C_s − W_u·C_u − W_c·C_c − W_h·C_h)

computed as a vectorised dot product. Hard gates force ``S = 0`` and REJECTED
regardless of arithmetic.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np

from config.settings import settings
from src.models.scoring import HardGate, ScoreBreakdown, ScoreComponent
from src.scoring.weights import KEYS, base_score, weight_vector


def route(score: float, gate: bool) -> str:
    if gate or score < settings.uncertain_threshold:
        return "REJECTED"
    if score >= settings.trusted_threshold:
        return "TRUSTED"
    return "UNCERTAIN"


def compute(counts: Dict[str, float], gates: List[Tuple[str, bool, str]] | None = None) -> ScoreBreakdown:
    c = np.array([float(counts.get(k, 0.0)) for k in KEYS])
    w = weight_vector()
    deltas = w * c
    raw = base_score() + float(deltas.sum())
    gate_models = [HardGate(gate=g, triggered=t, reason=r) for g, t, r in (gates or [])]
    triggered = any(g.triggered for g in gate_models)
    final = 0.0 if triggered else float(np.clip(raw, 0.0, 100.0))
    comps = [ScoreComponent(key=k, weight=float(w[i]), count=float(c[i]), delta=float(deltas[i])) for i, k in enumerate(KEYS)]  # type: ignore[arg-type]
    return ScoreBreakdown(base=base_score(), components=comps, raw=round(raw, 2), final=round(final, 2), gates=gate_models, gate_triggered=triggered, verdict=route(final, triggered))  # type: ignore[arg-type]


def aggregate(scores: List[float]) -> float:
    return round(float(np.mean(scores)), 2) if scores else 100.0
