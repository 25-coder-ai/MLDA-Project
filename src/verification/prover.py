"""Bounded proof engine — the answer to *"prove this code is correct"*.

TRUST-CODE never accepts a correctness claim on the model's word. A correctness claim
is converted into a *proof obligation* (call, post-condition, input domain) that is
discharged in the sandbox by exhaustive enumeration when the domain is small enough
(bounded model checking over a finite domain) or by deterministic seeded sampling
otherwise. The outcome is a tamper-evident certificate:

* ``PROVED_BOUNDED``  — holds for *every* input in the declared finite domain
* ``REFUTED``         — a concrete counterexample was found
* ``INCONCLUSIVE``    — only sampled; no counterexample, but no proof either
* ``ERROR``           — the obligation could not be evaluated

Certificates always state their scope: a bounded proof is not a proof for all inputs.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import settings
from src.ingestion.ast_parser import SymbolIndex
from src.models.evidence import ProofCertificate, ProofSpec

STATUS = {"proved": "PROVED_BOUNDED", "refuted": "REFUTED", "inconclusive": "INCONCLUSIVE"}


def proof_task(task_id: str, spec: ProofSpec, imports: List[str], setup: Optional[str]) -> Dict:
    return {
        "id": task_id,
        "kind": "prove",
        "imports": imports,
        "setup": setup,
        "expr": spec.expr,
        "post": spec.post,
        "pre": spec.pre,
        "domain": spec.domain,
        "allow": spec.allow,
        "exhaustive_limit": settings.proof_exhaustive_limit,
        "sample": settings.proof_sample_size,
        "budget_s": 25,
    }


def _function_source(ix: Optional[SymbolIndex], qualified: str) -> str:
    if ix is None:
        return ""
    sym = ix.get(qualified)
    if sym is None:
        return ""
    lines = ix.sources.get(sym.file, "").splitlines()
    return "\n".join(lines[sym.line - 1 : sym.end_line])


def certificate(raw: Dict, spec: ProofSpec, function: str, req_id: Optional[str], ix: Optional[SymbolIndex]) -> ProofCertificate:
    status = STATUS.get(raw.get("status", ""), "ERROR")
    prop = f"{spec.expr} ⇒ {spec.post}" if spec.post else spec.expr
    cert = ProofCertificate(
        req_id=req_id,
        function=function,
        property=prop,
        domain=spec.domain or "(none)",
        precondition=spec.pre,
        status=status,  # type: ignore[arg-type]
        cases_checked=int(raw.get("cases", 0)),
        domain_size=int(raw.get("domain_size", 0)),
        exhaustive=bool(raw.get("exhaustive", False)),
        counterexample=raw.get("counterexample"),
        detail=raw.get("detail", ""),
    )
    if status == "PROVED_BOUNDED":
        cert.disclaimer = f"Holds for all {cert.cases_checked:,} inputs of the finite domain. This is a bounded proof: behaviour outside the declared domain is not covered."
    elif status == "REFUTED":
        cert.disclaimer = "Refuted by a concrete, reproducible counterexample. The correctness claim is false."
    elif status == "INCONCLUSIVE":
        cert.disclaimer = f"{cert.cases_checked:,} sampled inputs passed, but the domain ({cert.domain_size:,}) was not exhausted — no proof."
    else:
        cert.disclaimer = "The obligation could not be evaluated; correctness remains unproven."
    material = "|".join([_function_source(ix, function), prop, cert.domain, str(cert.precondition), status, str(cert.cases_checked), str(cert.counterexample)])
    cert.certificate_hash = hashlib.sha256(material.encode()).hexdigest()
    return cert


def prove_adhoc(workdir: Path, function: str, spec: ProofSpec, setup: Optional[str], ix: Optional[SymbolIndex]) -> ProofCertificate:
    from src.sandbox.runner import run_harness

    raw = run_harness(workdir, "tasks", {"tasks": [proof_task("adhoc", spec, [function], setup)]})
    res = raw.get("results", {}).get("adhoc") or {"status": "error", "detail": raw.get("_error", "no result")}
    return certificate(res, spec, function, None, ix)
