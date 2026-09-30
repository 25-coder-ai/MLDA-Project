"""End-to-end TRUST-CODE pipeline (SOP §4): Input → Evidence → Seal → Verify → Score → Output."""
from __future__ import annotations

import hashlib
import time
import uuid
from typing import Callable, Dict, List, Optional

from config.settings import settings
from src.evidence.evidence_store import GroundTruthDatabase
from src.evidence.ledger import EvidenceLedger
from src.evidence.static_analyzer import analyse, tool_availability
from src.evidence.test_runner import has_tests, run_suite
from src.ingestion.ast_parser import _ts_get_parser, build_symbol_index, language_of
from src.ingestion.git_loader import cleanup_session, load_workspace, materialise
from src.ingestion.spec_indexer import RequirementIndex, chunk_documents, parse_requirements
from src.models.evidence import TestSuiteResult
from src.models.ingestion import AIEvaluationPayload, AIReviewClaim, RequirementDoc
from src.models.scoring import FailureModeRow, TrustReport, TrustSummary, WorkspaceInfo
from src.sandbox.runner import backend_name
from src.scoring.score_engine import aggregate
from src.verification.call_graph import CallGraph
from src.verification.claim_extractor import parse_review_text
from src.verification.claim_verifier import ClaimVerifier
from src.verification.patch_validator import PatchValidator
from src.verification.requirement_verifier import evaluate_requirements
from src.verification.verifier_audit import audit

FAILURE_MODES = [
    ("hallucinated_reference", "Hallucinated / nonexistent code references", "Files, line ranges, modules, requirement IDs or patch context that do not exist."),
    ("fake_symbol", "Fake / nonexistent symbols", "Functions, parameters, attributes or APIs — cited in a review or called by a fix — that are absent from the AST."),
    ("unsupported_claim", "Unsupported review claims", "Assertions no AST fact, static finding, test, requirement or proof corroborates."),
    ("semantic_hallucination", "Semantic hallucinations", "Real symbols, false statements about what they do."),
    ("requirement_mismatch", "Code vs requirement mismatch", "Code that fails executable acceptance clauses, or claims that misstate compliance."),
    ("bad_fix", "Bad AI-generated fixes", "Patches that regress tests, inject vulnerabilities, break syntax or call phantom code."),
    ("inadequate_tests", "Tests don't cover the issue", "Changed lines never executed by tests, or mutants of the fix survive."),
    ("verifier_same_mistake", "LLM verifier makes the same mistake", "An LLM judge confirms errors the engine proved false (correlated failure)."),
    ("confirmation_bias", "LLM confirmation bias", "A judge agrees with a claim and its negation, or restates it without evidence."),
    ("novel_bug", "Unknown / novel bugs", "Unexplained behaviour changes or new findings nobody reported, found by differential probing."),
    ("unproven_correctness", "“Prove this code is correct”", "Correctness assertions lacking — or refuted by — a proof certificate."),
]

STAGES = [
    ("ingest", "Ingestion & AST"),
    ("evidence", "Evidence gathering"),
    ("seal", "Seal ground truth"),
    ("claims", "Claim verification"),
    ("patches", "Patch validation"),
    ("audit", "Verifier audit"),
    ("score", "Scoring & routing"),
]

Event = Dict[str, object]


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def capabilities() -> Dict[str, object]:
    tools = tool_availability()
    try:
        import spacy  # noqa: F401

        nlp = "spacy+regex"
    except Exception:
        nlp = "regex (deterministic)"
    return {
        "engine": f"{settings.app_name} {settings.version}",
        "sandbox": backend_name(),
        "parsers": {"python": "stdlib ast", "polyglot": "tree-sitter" if _ts_get_parser else "lexical fallback"},
        "sast": ["trustcode-sast"] + [t for t in ("bandit", "semgrep") if tools.get(t)],
        "tools": tools,
        "nlp": nlp,
        "weights": settings.weights.model_dump(),
        "thresholds": {"trusted": settings.trusted_threshold, "uncertain": settings.uncertain_threshold},
    }


class TrustPipeline:
    def __init__(self, on_event: Optional[Callable[[Event], None]] = None) -> None:
        self.on_event = on_event or (lambda e: None)

    def _emit(self, stage: str, status: str, message: str = "") -> None:
        order = [s for s, _ in STAGES]
        pct = int(100 * (order.index(stage) + (1 if status == "done" else 0.4)) / len(order)) if stage in order else 0
        self.on_event({"stage": stage, "status": status, "message": message, "progress": pct, "t": time.time()})

    def run(self, payload: AIEvaluationPayload) -> TrustReport:
        t0 = time.time()
        warnings: List[str] = []
        ledger = EvidenceLedger()

        # ------------------------------------------------------------ Module 1
        self._emit("ingest", "running", "loading workspace")
        snap = load_workspace(payload.workspace)
        files = snap.files
        req_docs = payload.requirements or [RequirementDoc(name=p, content=c) for p, c in snap.spec_documents().items()]
        ix = build_symbol_index(files)
        for f, err in ix.parse_errors.items():
            warnings.append(f"{f}: {err}")
        req_nodes = parse_requirements(req_docs)
        chunks = chunk_documents(req_docs)
        req_index = RequirementIndex(req_nodes)
        ledger.append("ingest", "WORKSPACE", "git_loader", {p: _sha(c) for p, c in sorted(files.items())}, f"{len(files)} files snapshotted from {snap.source}")
        ledger.append("ingest", "SYMBOL_TABLE", "ast_parser", sorted(ix.symbols), f"{len(ix.symbols)} symbols, {len(ix.references)} references")
        ledger.append("ingest", "SPECIFICATION", "spec_indexer", [n.model_dump() for n in req_nodes], f"{len(req_nodes)} requirement nodes, {len(chunks)} chunks ({req_index.backend})")
        self._emit("ingest", "done", f"{len(ix.symbols)} symbols · {len(req_nodes)} requirements")

        # ------------------------------------------------------------ Module 2
        self._emit("evidence", "running", "static analysis")
        session_dir = settings.work_root / snap.session_id
        baseline_dir = materialise(files, session_dir / "baseline")
        findings, tool_status = analyse(baseline_dir, files, ix.trees)
        ledger.append("evidence", "SAST", "+".join(k for k, v in tool_status.items() if v == "ok"), [f.model_dump() for f in findings], f"{len(findings)} static findings")
        graph = CallGraph(ix)
        ledger.append("evidence", "CALL_GRAPH", "networkx", sorted(map(list, graph.g.edges())), f"{graph.g.number_of_nodes()} nodes, {graph.g.number_of_edges()} edges")
        tests = TestSuiteResult()
        if payload.options.run_tests and has_tests(list(files)):
            self._emit("evidence", "running", "baseline test suite")
            tests = run_suite(baseline_dir, trace=True, junit_name="baseline_test_results.xml")
            if tests.error:
                warnings.append(f"baseline tests could not run: {tests.error}")
        elif payload.options.run_tests:
            warnings.append("no test files found; regression checks are limited to requirements and static analysis")
        ledger.append("evidence", "BASELINE_TESTS", tests.framework, [(t.test_id, t.outcome) for t in tests.tests], f"{tests.counts().get('passed', 0)}/{len(tests.tests)} passing")
        exec_nodes = [n for n in req_nodes if n.executable]
        self._emit("evidence", "running", "requirement acceptance clauses")
        req_status = evaluate_requirements(exec_nodes, baseline_dir, findings, ix, run_proofs=payload.options.run_proofs)
        for n in req_nodes:
            if n.req_id not in req_status:
                from src.models.evidence import RequirementStatus

                req_status[n.req_id] = RequirementStatus(req_id=n.req_id, title=n.title, text=n.text, symbols=n.symbols, status="UNVERIFIABLE")
        ledger.append("evidence", "REQUIREMENTS", "requirement_verifier", {k: v.status for k, v in sorted(req_status.items())}, ", ".join(f"{k}:{v.status}" for k, v in sorted(req_status.items())) or "no requirements")
        db = GroundTruthDatabase(files=files, index=ix, findings=findings, tests=tests, requirements=req_status, req_index=req_index, call_graph=graph, tool_status=tool_status)
        self._emit("evidence", "done", f"{len(findings)} findings · {len(tests.tests)} tests")

        # ------------------------------------------------------------ seal
        seal = ledger.seal()
        self._emit("seal", "done", f"sealed {seal.hash[:12]}…")

        # ------------------------------------------------------------ Module 3
        self._emit("claims", "running", "parsing AI review")
        claims: List[AIReviewClaim] = list(payload.claims) + (parse_review_text(payload.review_text) if payload.review_text.strip() else [])
        seen_ids: Dict[str, int] = {}
        for c in claims:
            n = seen_ids.get(c.claim_id, 0)
            seen_ids[c.claim_id] = n + 1
            if n:
                c.claim_id = f"{c.claim_id}-{n + 1}"
        ledger.append("verify", "CLAIMS_RECEIVED", "claim_extractor", [c.model_dump() for c in claims], f"{len(claims)} AI claims received after seal")
        verifier = ClaimVerifier(db, graph)
        verdicts = []
        for c in claims:
            v = verifier.verify(c)
            verdicts.append(v)
            ledger.append("verify", "CLAIM_VERDICT", "claim_verifier", {"claim": c.claim_id, "state": v.state, "score": v.score.final}, f"{c.claim_id}: {v.state} ({v.score.final:.0f})")
        self._emit("claims", "done", f"{len(verdicts)} claims judged")

        # ------------------------------------------------------------ Module 4
        patch_verdicts = []
        if payload.patches:
            validator = PatchValidator(db, graph, baseline_dir, session_dir, req_nodes, payload.options, log=lambda m: self._emit("patches", "running", m))
            for i, p in enumerate(payload.patches):
                self._emit("patches", "running", f"validating {p.patch_id} ({i + 1}/{len(payload.patches)})")
                pv = validator.validate(p, i)
                patch_verdicts.append(pv)
                ledger.append("verify", "PATCH_VERDICT", "patch_validator", {"patch": p.patch_id, "verdict": pv.score.verdict, "score": pv.score.final}, f"{p.patch_id}: {pv.score.verdict} ({pv.score.final:.0f})")
        self._emit("patches", "done", f"{len(patch_verdicts)} patches validated")

        # ------------------------------------------------------------ verifier audit
        self._emit("audit", "running", "auditing LLM judge")
        vaudit = audit(verdicts, payload.verifier)
        if vaudit:
            ledger.append("verify", "VERIFIER_AUDIT", "verifier_audit", vaudit.metrics.model_dump(), vaudit.conclusion[:140])
        self._emit("audit", "done", "judge audited" if vaudit else "no judge transcript supplied")

        # ------------------------------------------------------------ Module 5
        rows = []
        for key, label, desc in FAILURE_MODES:
            refs = [f"claim:{v.claim.claim_id}" for v in verdicts if key in v.failure_modes] + [f"patch:{p.patch.patch_id}" for p in patch_verdicts if key in p.failure_modes]
            rows.append(FailureModeRow(key=key, label=label, description=desc, detected=len(refs), refs=refs))
        items = [v.score for v in verdicts] + [p.score for p in patch_verdicts]
        trusted = sum(1 for s in items if s.verdict == "TRUSTED")
        uncertain = sum(1 for s in items if s.verdict == "UNCERTAIN")
        rejected = sum(1 for s in items if s.verdict == "REJECTED")
        gates = sum(1 for s in items if s.gate_triggered)
        detected = [r.label for r in rows if r.detected]
        headline = (f"{trusted} of {len(items)} AI outputs are trustworthy; {rejected} rejected, {uncertain} need human review." if items else "No AI claims or patches were supplied.")
        if detected:
            headline += f" Detected: {', '.join(detected[:4])}{'…' if len(detected) > 4 else ''}."
        summary = TrustSummary(trust_index=aggregate([s.final for s in items]), trusted=trusted, uncertain=uncertain, rejected=rejected, claims=len(verdicts), patches=len(patch_verdicts), hard_gates=gates, headline=headline)
        ledger.append("score", "VERDICTS", "score_engine", summary.model_dump(), headline[:140])
        self._emit("score", "done", f"trust index {summary.trust_index:.0f}")

        langs: Dict[str, int] = {}
        for f in files:
            lang = language_of(f)
            if lang:
                langs[lang] = langs.get(lang, 0) + 1
        for tool, st in tool_status.items():
            if st not in ("ok", "unavailable"):
                warnings.append(f"{tool}: {st}")
        report = TrustReport(
            report_id="TR-" + uuid.uuid4().hex[:10].upper(),
            engine=f"{settings.app_name} {settings.version}",
            created_at=time.time(),
            duration_ms=int((time.time() - t0) * 1000),
            workspace=WorkspaceInfo(source=snap.source, files=sorted(files), languages=langs, loc=sum(len(c.splitlines()) for c in files.values()), commit=snap.commit),
            capabilities={**capabilities(), "tool_status": tool_status, "retrieval": req_index.backend},
            ledger=ledger.model(),
            symbol_tree=ix.to_tree(),
            call_graph=graph.model(),
            static_findings=findings,
            baseline_tests=tests,
            requirements=[req_status[k] for k in sorted(req_status)],
            claims=verdicts,
            patches=patch_verdicts,
            verifier_audit=vaudit,
            failure_modes=rows,
            summary=summary,
            files=files,
            warnings=warnings,
        )
        cleanup_session(snap.session_id)
        return report
