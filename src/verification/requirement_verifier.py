"""Specification & contract compliance (SOP Objective 3).

Requirement nodes carrying executable clauses are evaluated deterministically against
a workspace (baseline or patched):

* ``check:``  — acceptance assertion evaluated in the sandbox
* ``prove:``  — bounded proof obligation (see ``prover.py``)
* ``forbid:`` — a static rule id that must not fire inside the requirement's symbols

A requirement is PASS only when every clause passes, FAIL when any clause fails and
UNVERIFIABLE when it has no executable clause (prose only).
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from src.ingestion.ast_parser import SymbolIndex
from src.models.evidence import RequirementCheckResult, RequirementNode, RequirementStatus, StaticFinding
from src.sandbox.runner import run_harness
from src.verification.prover import certificate, proof_task


def _in_scope(f: StaticFinding, node: RequirementNode, ix: SymbolIndex) -> bool:
    if not node.symbols:
        return True
    for q in node.symbols:
        sym = ix.get(q)
        if sym is not None and sym.file == f.file and sym.line <= f.line <= sym.end_line:
            return True
        if sym is None and (f.function or "").endswith(q.split(".")[-1]):
            return True
    return False


def evaluate_requirements(nodes: List[RequirementNode], workdir: Path, findings: List[StaticFinding], ix: SymbolIndex, run_proofs: bool = True, only: Optional[List[str]] = None) -> Dict[str, RequirementStatus]:
    tasks = []
    for n in nodes:
        if only is not None and n.req_id not in only:
            continue
        for i, expr in enumerate(n.checks):
            tasks.append({"id": f"{n.req_id}#c{i}", "kind": "check", "imports": n.symbols, "setup": n.setup, "expr": expr, "budget_s": 10})
        if run_proofs:
            for i, spec in enumerate(n.proofs):
                tasks.append(proof_task(f"{n.req_id}#p{i}", spec, n.symbols, n.setup))
    raw = run_harness(workdir, "tasks", {"tasks": tasks}) if tasks else {"results": {}}
    results = raw.get("results", {})
    sandbox_error = raw.get("_error") or raw.get("fatal")

    out: Dict[str, RequirementStatus] = {}
    for n in nodes:
        if only is not None and n.req_id not in only:
            continue
        items: List[RequirementCheckResult] = []
        proofs = []
        for i, expr in enumerate(n.checks):
            r = results.get(f"{n.req_id}#c{i}") or {"status": "error", "detail": sandbox_error or "no result"}
            items.append(RequirementCheckResult(kind="check", expr=expr, status=r.get("status", "error"), detail=r.get("detail", "")))
        if run_proofs:
            for i, spec in enumerate(n.proofs):
                r = results.get(f"{n.req_id}#p{i}") or {"status": "error", "detail": sandbox_error or "no result"}
                cert = certificate(r, spec, n.symbols[0] if n.symbols else "?", n.req_id, ix)
                proofs.append(cert)
                status = {"PROVED_BOUNDED": "pass", "REFUTED": "fail", "INCONCLUSIVE": "skipped"}.get(cert.status, "error")
                items.append(RequirementCheckResult(kind="prove", expr=cert.property, status=status, detail=cert.disclaimer if cert.status != "REFUTED" else f"counterexample {cert.counterexample}"))  # type: ignore[arg-type]
        for rule in n.forbids:
            hits = [f for f in findings if (f.rule_id == rule or f.cwe == rule) and _in_scope(f, n, ix)]
            items.append(RequirementCheckResult(kind="forbid", expr=rule, status="fail" if hits else "pass", detail=(f"{hits[0].rule_id} at {hits[0].file}:{hits[0].line} — {hits[0].message}" if hits else f"no {rule} finding in scope")))
        if not items:
            status = "UNVERIFIABLE"
        elif any(i.status in ("fail", "error") for i in items):
            status = "FAIL"
        else:
            status = "PASS"
        out[n.req_id] = RequirementStatus(req_id=n.req_id, title=n.title, text=n.text, symbols=n.symbols, status=status, results=items, proofs=proofs)  # type: ignore[arg-type]
    return out
