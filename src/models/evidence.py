"""Module 2 — ground-truth evidence entities (AST symbols, SAST findings, tests, ledger, specs)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["INFO", "LOW", "MEDIUM", "HIGH"]
SOP_LEVEL = {"INFO": "INFO", "LOW": "INFO", "MEDIUM": "WARNING", "HIGH": "ERROR"}


class Symbol(BaseModel):
    name: str
    qualified_name: str
    kind: str  # module|class|function|method|parameter|variable|global|class_attribute|attribute|import
    file: str
    line: int
    end_line: int
    scope: str = ""
    signature: Optional[str] = None
    detail: Optional[str] = None
    language: str = "python"


class Reference(BaseModel):
    name: str
    file: str
    line: int
    scope: str
    ctx: Literal["load", "call", "attr"]


class StaticFinding(BaseModel):
    finding_id: str
    tool: str
    rule_id: str
    file: str
    line: int
    end_line: int
    severity: Severity
    sop_level: Literal["INFO", "WARNING", "ERROR"] = "INFO"
    confidence: str = "HIGH"
    cwe: Optional[str] = None
    category: str = "Security"
    security: bool = True
    message: str
    code: str = ""
    function: Optional[str] = None
    fingerprint: str = ""


class TestCaseResult(BaseModel):
    __test__ = False  # not a pytest test class

    test_id: str
    outcome: Literal["passed", "failed", "error", "skipped", "missing"]
    message: str = ""
    duration: float = 0.0
    lines: Dict[str, List[int]] = Field(default_factory=dict)


class TestSuiteResult(BaseModel):
    __test__ = False  # not a pytest test class

    framework: str = "none"
    ran: bool = False
    tests: List[TestCaseResult] = Field(default_factory=list)
    collection_errors: List[Dict[str, str]] = Field(default_factory=list)
    duration_ms: int = 0
    error: Optional[str] = None
    junit_xml: Optional[str] = None

    def by_id(self) -> Dict[str, TestCaseResult]:
        return {t.test_id: t for t in self.tests}

    def counts(self) -> Dict[str, int]:
        out = {"passed": 0, "failed": 0, "error": 0, "skipped": 0, "missing": 0}
        for t in self.tests:
            out[t.outcome] += 1
        out["total"] = len(self.tests)
        return out


class LedgerEntry(BaseModel):
    seq: int
    stage: str
    kind: str
    source: str
    summary: str
    digest: str
    prev_hash: str
    hash: str
    timestamp: float


class LedgerModel(BaseModel):
    algorithm: str = "sha256"
    formula: str = "sha256(f'{seq}|{stage}|{kind}|{source}|{digest}|{prev_hash}')"
    entries: List[LedgerEntry] = Field(default_factory=list)
    seal_seq: Optional[int] = None
    seal_hash: Optional[str] = None
    head_hash: str = ""
    intact: bool = True


class ProofSpec(BaseModel):
    expr: str
    post: Optional[str] = None
    domain: str = ""
    pre: Optional[str] = None
    allow: List[str] = Field(default_factory=list)


class RequirementNode(BaseModel):
    req_id: str
    title: str
    text: str
    symbols: List[str] = Field(default_factory=list)
    setup: Optional[str] = None
    checks: List[str] = Field(default_factory=list)
    forbids: List[str] = Field(default_factory=list)
    proofs: List[ProofSpec] = Field(default_factory=list)
    source_doc: str = ""
    line: int = 0

    @property
    def executable(self) -> bool:
        return bool(self.checks or self.forbids or self.proofs)


class SpecChunk(BaseModel):
    chunk_id: str
    doc: str
    text: str
    tokens: int
    req_ids: List[str] = Field(default_factory=list)


class ProofCertificate(BaseModel):
    req_id: Optional[str] = None
    function: str
    property: str
    domain: str
    precondition: Optional[str] = None
    status: Literal["PROVED_BOUNDED", "REFUTED", "INCONCLUSIVE", "ERROR"]
    cases_checked: int = 0
    domain_size: int = 0
    exhaustive: bool = False
    counterexample: Optional[Dict[str, Any]] = None
    detail: str = ""
    certificate_hash: str = ""
    disclaimer: str = ""


class RequirementCheckResult(BaseModel):
    kind: Literal["check", "forbid", "prove"]
    expr: str
    status: Literal["pass", "fail", "error", "skipped"]
    detail: str = ""


class RequirementStatus(BaseModel):
    req_id: str
    title: str
    text: str = ""
    symbols: List[str] = Field(default_factory=list)
    status: Literal["PASS", "FAIL", "UNVERIFIABLE"]
    results: List[RequirementCheckResult] = Field(default_factory=list)
    proofs: List[ProofCertificate] = Field(default_factory=list)


class CallGraphNode(BaseModel):
    id: str
    label: str
    kind: str
    file: Optional[str] = None
    line: Optional[int] = None


class CallGraphEdge(BaseModel):
    source: str
    target: str
    kind: Literal["call", "import"] = "call"


class CallGraphModel(BaseModel):
    nodes: List[CallGraphNode] = Field(default_factory=list)
    edges: List[CallGraphEdge] = Field(default_factory=list)
