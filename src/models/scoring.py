"""Module 3/4/5 — verification results, trust-score breakdown and the final report."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from src.models.evidence import (
    CallGraphModel,
    LedgerModel,
    ProofCertificate,
    RequirementStatus,
    StaticFinding,
    TestSuiteResult,
)
from src.models.ingestion import AIPatchPayload, AIReviewClaim

ClaimState = Literal["SUPPORTED", "CONTRADICTED", "UNSUPPORTED", "HALLUCINATED"]
Verdict = Literal["TRUSTED", "UNCERTAIN", "REJECTED"]


# --------------------------------------------------------------------------- score
class ScoreComponent(BaseModel):
    key: Literal["supported", "unsupported", "contradicted", "hallucinated"]
    weight: float
    count: float
    delta: float


class HardGate(BaseModel):
    gate: str
    triggered: bool
    reason: str = ""


class ScoreBreakdown(BaseModel):
    base: float
    components: List[ScoreComponent]
    raw: float
    final: float
    gates: List[HardGate] = Field(default_factory=list)
    gate_triggered: bool = False
    verdict: Verdict
    formula: str = "S = clamp(0, 100, S_base + W_s*C_s - W_u*C_u - W_c*C_c - W_h*C_h)"


# --------------------------------------------------------------------------- claims
class SymbolMatch(BaseModel):
    qualified_name: str
    kind: str
    file: str
    line: int


class SymbolResolution(BaseModel):
    token: str
    status: Literal["resolved", "resolved_elsewhere", "builtin", "external", "hallucinated"]
    role: Literal["symbol", "parameter"] = "symbol"
    matches: List[SymbolMatch] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    note: str = ""


class LocationCheck(BaseModel):
    file_exists: bool
    file_lines: int = 0
    lines_valid: bool = True
    anchored_symbol: Optional[str] = None
    drift_lines: Optional[int] = None
    note: str = ""


class PredicateResult(BaseModel):
    kind: str
    label: str
    subject: Optional[str] = None
    asserted: Optional[bool] = None
    actual: Optional[bool] = None
    outcome: Literal["corroborated", "refuted", "unknown", "unsupported_impact"]
    evidence: str = ""
    lines: List[int] = Field(default_factory=list)
    phrase: str = ""


class RequirementLink(BaseModel):
    req_id: str
    title: str
    similarity: float
    explicit: bool
    status: str
    asserted_satisfied: Optional[bool] = None


class VerifierFlag(BaseModel):
    code: str
    label: str
    detail: str = ""


class ClaimVerifierView(BaseModel):
    verdict: str
    counterfactual_verdict: Optional[str] = None
    correct: Optional[bool] = None
    flags: List[VerifierFlag] = Field(default_factory=list)
    echo_ratio: float = 0.0
    evidence_anchors: int = 0


class ClaimVerdict(BaseModel):
    claim: AIReviewClaim
    state: ClaimState
    symbols: List[SymbolResolution] = Field(default_factory=list)
    location: LocationCheck
    predicates: List[PredicateResult] = Field(default_factory=list)
    static_evidence: List[StaticFinding] = Field(default_factory=list)
    requirement_links: List[RequirementLink] = Field(default_factory=list)
    proofs: List[ProofCertificate] = Field(default_factory=list)
    evidence_counts: Dict[str, float] = Field(default_factory=dict)
    evidence_kinds: List[str] = Field(default_factory=list)
    score: ScoreBreakdown
    explanations: List[str] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)
    counterfactual_probe: str = ""
    verifier: Optional[ClaimVerifierView] = None


# --------------------------------------------------------------------------- patches
class HunkReport(BaseModel):
    file: str
    header: str
    status: Literal["applied", "offset", "rejected"]
    offset: int = 0
    note: str = ""


class PatchApplication(BaseModel):
    applied: bool
    method: str = "trustcode-applier"
    git_check: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    hunks: List[HunkReport] = Field(default_factory=list)
    files_touched: List[str] = Field(default_factory=list)
    changed_lines: Dict[str, List[int]] = Field(default_factory=dict)
    removed_lines: Dict[str, List[int]] = Field(default_factory=dict)
    snippet_found: Optional[bool] = None


class IntroducedSymbol(BaseModel):
    file: str
    line: int
    name: str
    kind: Literal["undefined_name", "missing_attribute", "unknown_keyword", "syntax_error"]
    detail: str = ""
    suggestions: List[str] = Field(default_factory=list)


class TestTransition(BaseModel):
    __test__ = False  # not a pytest test class

    test_id: str
    before: str
    after: str
    transition: Literal["regression", "fixed", "stable_pass", "stable_fail", "new_pass", "new_fail", "removed", "skipped"]
    message: str = ""


class TestComparison(BaseModel):
    __test__ = False  # not a pytest test class

    ran: bool = False
    baseline: Dict[str, int] = Field(default_factory=dict)
    post: Dict[str, int] = Field(default_factory=dict)
    matrix: List[TestTransition] = Field(default_factory=list)
    regressions: List[str] = Field(default_factory=list)
    fixed: List[str] = Field(default_factory=list)
    targets_requested: List[str] = Field(default_factory=list)
    targets_satisfied: List[str] = Field(default_factory=list)
    targets_unsatisfied: List[str] = Field(default_factory=list)
    post_suite: Optional[TestSuiteResult] = None


class RequirementTransition(BaseModel):
    req_id: str
    title: str
    before: str
    after: str
    transition: Literal["regression", "fixed", "still_failing", "passing", "unverifiable"]
    linked: bool = False
    details: List[str] = Field(default_factory=list)


class Mutant(BaseModel):
    mutant_id: str
    file: str
    line: int
    operator: str
    original: str
    mutated: str
    status: Literal["killed", "survived", "timeout", "error"]
    killed_by: List[str] = Field(default_factory=list)


class TestAdequacy(BaseModel):
    __test__ = False  # not a pytest test class

    changed_executable_lines: List[int] = Field(default_factory=list)
    covered_lines: List[int] = Field(default_factory=list)
    coverage_ratio: Optional[float] = None
    covering_tests: List[str] = Field(default_factory=list)
    mutants: List[Mutant] = Field(default_factory=list)
    mutation_score: Optional[float] = None
    verdict: Literal["adequate", "weak", "none", "not_applicable"] = "not_applicable"
    note: str = ""


class Divergence(BaseModel):
    input: str
    before: str
    after: str
    kind: Literal["new_crash", "new_hang", "silent_value_change", "exception_changed", "crash_fixed", "validation_added"]


class FunctionProbe(BaseModel):
    qualified_name: str
    cases_tried: int = 0
    divergences: List[Divergence] = Field(default_factory=list)
    skipped_reason: Optional[str] = None


class BehaviorProbe(BaseModel):
    functions: List[FunctionProbe] = Field(default_factory=list)
    novel_bug_count: int = 0
    intended_changes: int = 0


class ImpactedFunction(BaseModel):
    qualified_name: str
    distance: int
    covered_by_tests: bool


class BlastRadius(BaseModel):
    changed_functions: List[str] = Field(default_factory=list)
    impacted: List[ImpactedFunction] = Field(default_factory=list)


class PatchVerdict(BaseModel):
    patch: AIPatchPayload
    application: PatchApplication
    syntax_ok: bool = True
    syntax_error: Optional[str] = None
    introduced_symbols: List[IntroducedSymbol] = Field(default_factory=list)
    static_new: List[StaticFinding] = Field(default_factory=list)
    static_resolved: List[StaticFinding] = Field(default_factory=list)
    tests: TestComparison = Field(default_factory=TestComparison)
    requirements: List[RequirementTransition] = Field(default_factory=list)
    adequacy: TestAdequacy = Field(default_factory=TestAdequacy)
    behavior: BehaviorProbe = Field(default_factory=BehaviorProbe)
    blast_radius: BlastRadius = Field(default_factory=BlastRadius)
    proofs: List[ProofCertificate] = Field(default_factory=list)
    patched_files: Dict[str, str] = Field(default_factory=dict)
    evidence_counts: Dict[str, float] = Field(default_factory=dict)
    score: ScoreBreakdown
    explanations: List[str] = Field(default_factory=list)
    failure_modes: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- verifier audit
class VerifierMetrics(BaseModel):
    judged: int = 0
    accuracy: Optional[float] = None
    confirm_rate: float = 0.0
    true_support_rate: float = 0.0
    agreement_inflation: float = 0.0
    same_mistake_rate: Optional[float] = None
    error_correlation_phi: Optional[float] = None
    independence_index: Optional[float] = None
    polarity_insensitive_rate: Optional[float] = None
    confusion: Dict[str, int] = Field(default_factory=dict)


class VerifierAudit(BaseModel):
    metrics: VerifierMetrics
    entries: List[Dict[str, Any]] = Field(default_factory=list)
    conclusion: str = ""
    engine_heterogeneity: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- report
class FailureModeRow(BaseModel):
    key: str
    label: str
    description: str
    detected: int = 0
    refs: List[str] = Field(default_factory=list)


class TrustSummary(BaseModel):
    trust_index: float
    trusted: int
    uncertain: int
    rejected: int
    claims: int
    patches: int
    hard_gates: int
    headline: str


class WorkspaceInfo(BaseModel):
    source: str
    files: List[str]
    languages: Dict[str, int]
    loc: int
    commit: Optional[str] = None


class TrustReport(BaseModel):
    report_id: str
    engine: str
    created_at: float
    duration_ms: int
    workspace: WorkspaceInfo
    capabilities: Dict[str, Any]
    ledger: LedgerModel
    symbol_tree: Dict[str, List[Dict[str, Any]]]
    call_graph: CallGraphModel
    static_findings: List[StaticFinding]
    baseline_tests: TestSuiteResult
    requirements: List[RequirementStatus]
    claims: List[ClaimVerdict]
    patches: List[PatchVerdict]
    verifier_audit: Optional[VerifierAudit] = None
    failure_modes: List[FailureModeRow]
    summary: TrustSummary
    files: Dict[str, str] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)
