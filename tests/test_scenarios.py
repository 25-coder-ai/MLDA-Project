"""End-to-end: every built-in scenario must detect the failure modes it demonstrates."""
import pytest

from src.evidence.ledger import EvidenceLedger
from src.scenarios import SCENARIOS


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_scenario_detects_its_failure_modes(run_scenario, scenario):
    report = run_scenario(scenario["id"])
    detected = {row.key for row in report.failure_modes if row.detected}
    assert set(scenario["modes"]) <= detected
    assert report.ledger.intact and report.ledger.seal_seq is not None
    # claims are only received after the seal
    received = next(e.seq for e in report.ledger.entries if e.kind == "CLAIMS_RECEIVED")
    assert received > report.ledger.seal_seq


def test_claim_states(run_scenario):
    st = {c.claim.claim_id: c.state for c in run_scenario("unsupported-claims").claims}
    assert st == {"C1": "SUPPORTED", "C2": "UNSUPPORTED", "C3": "CONTRADICTED", "C4": "CONTRADICTED", "C5": "UNSUPPORTED", "C6": "SUPPORTED", "C7": "UNSUPPORTED"}
    sem = {c.claim.claim_id: c.state for c in run_scenario("semantic-hallucinations").claims}
    assert sem["C9"] == "SUPPORTED" and sem["C7"] == "UNSUPPORTED"
    assert all(sem[k] == "CONTRADICTED" for k in ("C1", "C2", "C3", "C4", "C5", "C6", "C8"))
    assert all(c.state == "HALLUCINATED" and c.score.verdict == "REJECTED" for c in run_scenario("hallucinated-references").claims)


def test_prove_correct_scenario(run_scenario):
    by = {c.claim.claim_id: c for c in run_scenario("prove-correct").claims}
    assert by["C1"].state == "CONTRADICTED" and any(p.status == "REFUTED" for p in by["C1"].proofs)
    assert by["C2"].state == "SUPPORTED" and by["C2"].score.verdict == "UNCERTAIN"  # bounded proof ≠ universal proof
    assert by["C3"].state == "CONTRADICTED" and by["C4"].state == "UNSUPPORTED"


def test_verifier_audit_metrics(run_scenario):
    a = run_scenario("verifier-same-mistake").verifier_audit
    assert a.metrics.same_mistake_rate == 1.0 and a.metrics.independence_index == 0.0
    b = run_scenario("confirmation-bias").verifier_audit
    assert b.metrics.polarity_insensitive_rate >= 0.5


def test_ledger_detects_tampering():
    led = EvidenceLedger()
    led.append("evidence", "SAST", "t", {"a": 1}, "x")
    led.seal()
    led.append("verify", "CLAIM", "t", {"b": 2}, "y")
    assert led.verify()
    led.entries[0].digest = "0" * 64
    assert not led.verify()
