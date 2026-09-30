"""Verifier independence audit — "the LLM verifier makes the same mistake".

LLM-as-a-judge pipelines assume the judge's errors are independent of the
generator's. TRUST-CODE measures that assumption against deterministic ground truth:

* **Correlated error** — how often the judge confirms claims the engine proved to be
  hallucinated or contradicted (``same_mistake_rate``) and the φ-coefficient between
  generator-error and judge-error indicators (``independence_index = 1 − φ``).
* **Shared hallucination** — the judge's rationale repeats a symbol that does not exist.
* **Confirmation bias** — using the engine-generated counterfactual (negated claim), a
  judge that confirms both a claim *and* its negation is polarity-insensitive; a
  rationale that mostly restates the claim with no evidence anchors (lines, tests,
  tools, resolvable symbols) is flagged as restatement-without-evidence.
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

from src.models.ingestion import VerifierJudgement
from src.models.scoring import ClaimVerdict, ClaimVerifierView, VerifierAudit, VerifierFlag, VerifierMetrics

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_STOP = frozenset("the and that this with from into have has for are was were will would should could not but its it's there their which when where what also very clearly indeed agree correct confirmed claim reviewer code function".split())
_ANCHOR = re.compile(r"\b(?:line|lines|l)\s*\d+|:\d+\b|\btest_\w+|\bpytest\b|\bbandit\b|\bsemgrep\b|\bcwe-\d+|\bast\b|\bcounterexample\b|\bstack ?trace\b|\bexecuted\b|\bran\b", re.I)


def _content(text: str) -> set:
    return {w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP}


def _phi(a: List[int], b: List[int]) -> Optional[float]:
    n11 = sum(1 for x, y in zip(a, b) if x and y)
    n00 = sum(1 for x, y in zip(a, b) if not x and not y)
    n10 = sum(1 for x, y in zip(a, b) if x and not y)
    n01 = sum(1 for x, y in zip(a, b) if not x and y)
    denom = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    if denom == 0:
        if a == b and len(a) > 0:
            return 1.0
        return None
    return round((n11 * n00 - n10 * n01) / denom, 3)


def audit(verdicts: List[ClaimVerdict], judgements: List[VerifierJudgement]) -> Optional[VerifierAudit]:
    if not judgements:
        return None
    by_id: Dict[str, ClaimVerdict] = {v.claim.claim_id: v for v in verdicts}
    entries: List[Dict] = []
    gen_wrong: List[int] = []
    ver_wrong: List[int] = []
    confusion = {"confirmed_true": 0, "confirmed_false": 0, "rejected_true": 0, "rejected_false": 0, "confirmed_unverifiable": 0, "rejected_unverifiable": 0}
    polarity_checked = polarity_bad = 0
    confirms = 0

    for j in judgements:
        v = by_id.get(j.claim_id)
        if v is None:
            continue
        flags: List[VerifierFlag] = []
        wrong_claim = v.state in ("HALLUCINATED", "CONTRADICTED")
        true_claim = v.state == "SUPPORTED"
        confirmed = j.verdict == "confirmed"
        confirms += int(confirmed)
        key = ("confirmed" if confirmed else "rejected") + ("_false" if wrong_claim else "_true" if true_claim else "_unverifiable")
        if j.verdict != "uncertain":
            confusion[key] = confusion.get(key, 0) + 1

        hallucinated_tokens = [s.token for s in v.symbols if s.status == "hallucinated"]
        rat = j.rationale or ""
        echo = 0.0
        rt, ct = _content(rat), _content(v.claim.claim_text)
        if rt:
            echo = round(len(rt & ct) / len(rt), 3)
        anchors = len(_ANCHOR.findall(rat))
        anchors += sum(1 for s in v.symbols if s.status == "resolved" and s.token in rat and s.token not in v.claim.claim_text)

        if confirmed and v.state == "HALLUCINATED":
            flags.append(VerifierFlag(code="echoed_hallucination", label="Echoed hallucination", detail="judge confirmed a claim that cites code which does not exist"))
        if confirmed and v.state == "CONTRADICTED":
            flags.append(VerifierFlag(code="echoed_contradiction", label="Echoed false claim", detail="judge confirmed a claim that deterministic evidence refutes"))
        shared = [t for t in hallucinated_tokens if t.split(".")[-1] in rat]
        if shared:
            flags.append(VerifierFlag(code="shared_hallucinated_symbol", label="Shared hallucinated symbol", detail=f"rationale repeats non-existent {', '.join('`' + s + '`' for s in shared)}"))
        if confirmed and v.state == "UNSUPPORTED":
            flags.append(VerifierFlag(code="ungrounded_confirmation", label="Ungrounded confirmation", detail="judge confirmed a claim for which no evidence exists"))
        if not confirmed and j.verdict == "rejected" and true_claim:
            flags.append(VerifierFlag(code="missed_true_defect", label="Missed true defect", detail="judge rejected a claim corroborated by deterministic evidence"))
        if j.counterfactual_verdict is not None:
            polarity_checked += 1
            if j.counterfactual_verdict == "confirmed" and confirmed:
                polarity_bad += 1
                flags.append(VerifierFlag(code="polarity_insensitive", label="Confirmation bias (polarity-insensitive)", detail=f"judge also confirmed the negated claim: “{v.counterfactual_probe}”"))
        if confirmed and echo >= 0.5 and anchors == 0:
            flags.append(VerifierFlag(code="restatement_without_evidence", label="Restatement without evidence", detail=f"{int(echo * 100)}% of the rationale's content words are copied from the claim; it cites no line, test, tool or symbol"))

        g = int(wrong_claim)
        vw = int((confirmed and wrong_claim) or (j.verdict == "rejected" and true_claim))
        if v.state != "UNSUPPORTED":
            gen_wrong.append(g)
            ver_wrong.append(vw)
        correct = None if v.state == "UNSUPPORTED" else (not bool(vw))
        v.verifier = ClaimVerifierView(verdict=j.verdict, counterfactual_verdict=j.counterfactual_verdict, correct=correct, flags=flags, echo_ratio=echo, evidence_anchors=anchors)
        for f in flags:
            if f.code in ("echoed_hallucination", "echoed_contradiction", "shared_hallucinated_symbol"):
                if "verifier_same_mistake" not in v.failure_modes:
                    v.failure_modes.append("verifier_same_mistake")
            if f.code in ("polarity_insensitive", "restatement_without_evidence", "ungrounded_confirmation"):
                if "confirmation_bias" not in v.failure_modes:
                    v.failure_modes.append("confirmation_bias")
        entries.append({"claim_id": j.claim_id, "model": j.model, "verdict": j.verdict, "counterfactual_verdict": j.counterfactual_verdict, "ground_truth": v.state, "correct": correct, "flags": [f.model_dump() for f in flags], "echo_ratio": echo, "anchors": anchors, "rationale": rat})

    judged = len(entries)
    if judged == 0:
        return None
    wrong_total = sum(gen_wrong)
    same = sum(1 for g, w in zip(gen_wrong, ver_wrong) if g and w)
    decided = len(gen_wrong)
    metrics = VerifierMetrics(
        judged=judged,
        accuracy=round(1 - sum(ver_wrong) / decided, 3) if decided else None,
        confirm_rate=round(confirms / judged, 3),
        true_support_rate=round(sum(1 for e in entries if e["ground_truth"] == "SUPPORTED") / judged, 3),
        same_mistake_rate=round(same / wrong_total, 3) if wrong_total else None,
        error_correlation_phi=_phi(gen_wrong, ver_wrong),
        polarity_insensitive_rate=round(polarity_bad / polarity_checked, 3) if polarity_checked else None,
        confusion=confusion,
    )
    metrics.agreement_inflation = round(metrics.confirm_rate - metrics.true_support_rate, 3)
    if metrics.error_correlation_phi is not None:
        metrics.independence_index = round(1 - max(0.0, metrics.error_correlation_phi), 3)

    parts = []
    if metrics.same_mistake_rate is not None:
        parts.append(f"the judge repeated {int(metrics.same_mistake_rate * 100)}% of the generator's proven errors")
    if metrics.independence_index is not None:
        parts.append(f"independence index {metrics.independence_index:.2f} (1.0 = errors uncorrelated)")
    if metrics.polarity_insensitive_rate:
        parts.append(f"{int(metrics.polarity_insensitive_rate * 100)}% of probed verdicts also confirmed the negated claim")
    if metrics.agreement_inflation > 0.2:
        parts.append(f"confirmation rate exceeds the true-claim rate by {int(metrics.agreement_inflation * 100)} points")
    risky = (metrics.same_mistake_rate or 0) >= 0.5 or (metrics.independence_index is not None and metrics.independence_index < 0.5) or (metrics.polarity_insensitive_rate or 0) >= 0.5
    conclusion = ("LLM-as-a-judge is NOT an independent check here: " if risky else "LLM judge behaved acceptably against ground truth: ") + "; ".join(parts or ["insufficient decided claims for statistics"]) + "."
    return VerifierAudit(
        metrics=metrics,
        entries=entries,
        conclusion=conclusion,
        engine_heterogeneity=[
            "AST symbol tables (Python ast / Tree-sitter)",
            "Deterministic SAST (trustcode-sast, Bandit, Semgrep when installed)",
            "Sandboxed dynamic execution (tests, probes, mutants)",
            "Executable specification clauses & bounded proofs",
            "Call-graph reachability (NetworkX)",
            "No stochastic model participates in any verdict",
        ],
    )
