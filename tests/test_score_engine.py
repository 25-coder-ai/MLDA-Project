import pytest

from src.scoring.score_engine import aggregate, compute, route


def test_formula_matches_sop():
    s = compute({"supported": 2})
    assert s.raw == 100 and s.final == 100 and s.verdict == "TRUSTED"
    s = compute({"supported": 1})
    assert s.final == 75 and s.verdict == "UNCERTAIN"
    s = compute({"unsupported": 1})
    assert s.final == 35 and s.verdict == "REJECTED"
    s = compute({"supported": 1, "contradicted": 1})
    assert s.final == 40
    s = compute({"hallucinated": 2})
    assert s.raw == -50 and s.final == 0


@pytest.mark.parametrize("gate", ["Test Regression Detected", "Security Vulnerability Injection", "Fatal Hallucination"])
def test_hard_gates_force_zero(gate):
    s = compute({"supported": 4}, [(gate, True, "reason")])
    assert s.final == 0 and s.verdict == "REJECTED" and s.gate_triggered


def test_untriggered_gates_do_not_change_score():
    s = compute({"supported": 2}, [("Fatal Hallucination", False, "")])
    assert s.final == 100 and not s.gate_triggered


def test_routing_boundaries():
    assert route(80.0, False) == "TRUSTED"
    assert route(79.99, False) == "UNCERTAIN"
    assert route(50.0, False) == "UNCERTAIN"
    assert route(49.99, False) == "REJECTED"
    assert route(100, True) == "REJECTED"


def test_aggregate():
    assert aggregate([0, 100]) == 50 and aggregate([]) == 100
