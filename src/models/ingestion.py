"""Module 1 — ingestion payload schemas (Step 1.4)."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkspaceSpec(BaseModel):
    """Where the code under review comes from. Exactly one source is used, in priority order."""

    files: Dict[str, str] = Field(default_factory=dict, description="Inline files: relative path -> content")
    repo_path: Optional[str] = Field(default=None, description="Local directory to snapshot")
    git_url: Optional[str] = Field(default=None, description="Remote repository to clone (GitPython)")
    ref: Optional[str] = Field(default=None, description="Branch / tag / commit to check out")

    @field_validator("files")
    @classmethod
    def _normalise_paths(cls, v: Dict[str, str]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for path, content in v.items():
            raw = path.replace("\\", "/").strip()
            if not raw or raw.startswith("/") or ":" in raw or ".." in raw.split("/"):
                raise ValueError(f"illegal workspace path: {path!r}")
            clean = raw[2:] if raw.startswith("./") else raw
            out[clean] = content.replace("\r\n", "\n")
        return out

    @model_validator(mode="after")
    def _one_source(self) -> "WorkspaceSpec":
        if not (self.files or self.repo_path or self.git_url):
            raise ValueError("workspace needs inline files, repo_path or git_url")
        return self


class RequirementDoc(BaseModel):
    name: str = "REQUIREMENTS.md"
    content: str


class AIReviewClaim(BaseModel):
    """Exact schema from SOP Step 1.4, plus provenance metadata."""

    claim_id: str = Field(default_factory=lambda: str(uuid4()))
    target_file: str
    target_line_start: int
    target_line_end: int
    referenced_symbols: List[str] = []
    claimed_defect_category: str  # e.g. 'Security', 'Bug', 'Performance', 'Style'
    claim_text: str
    source: Literal["structured", "parsed", "inferred"] = "structured"

    @model_validator(mode="after")
    def _order_lines(self) -> "AIReviewClaim":
        self.target_file = self.target_file.replace("\\", "/").lstrip("./")
        if self.target_line_end < self.target_line_start:
            self.target_line_start, self.target_line_end = self.target_line_end, self.target_line_start
        return self


class AIPatchPayload(BaseModel):
    """Exact schema from SOP Step 1.4, plus optional intent links used by Module 4."""

    patch_id: str = Field(default_factory=lambda: "P-" + uuid4().hex[:6])
    target_file: str
    original_code_snippet: str = ""
    suggested_diff: str
    intended_fix_description: str = ""
    fixes_tests: List[str] = Field(default_factory=list, description="Test ids the patch claims to make pass")
    requirement_ids: List[str] = Field(default_factory=list, description="Requirements the patch claims to satisfy")
    addresses_claims: List[str] = Field(default_factory=list)

    @field_validator("suggested_diff")
    @classmethod
    def _normalise_diff(cls, v: str) -> str:
        v = v.replace("\r\n", "\n")
        if not v.strip():
            raise ValueError("suggested_diff is empty")
        return v if v.endswith("\n") else v + "\n"


class VerifierJudgement(BaseModel):
    """Output of an *LLM-as-a-judge* that TRUST-CODE audits for correlated errors and bias."""

    claim_id: str
    verdict: Literal["confirmed", "rejected", "uncertain"]
    rationale: str = ""
    counterfactual_verdict: Optional[Literal["confirmed", "rejected", "uncertain"]] = Field(
        default=None, description="The same judge's verdict on the engine-generated negated claim"
    )
    model: Optional[str] = None


class VerificationOptions(BaseModel):
    run_tests: bool = True
    run_mutation: bool = True
    run_behavior_probe: bool = True
    run_proofs: bool = True
    max_mutants: Optional[int] = None


class AIEvaluationPayload(BaseModel):
    workspace: WorkspaceSpec
    requirements: List[RequirementDoc] = Field(default_factory=list)
    review_text: str = ""
    claims: List[AIReviewClaim] = Field(default_factory=list)
    patches: List[AIPatchPayload] = Field(default_factory=list)
    verifier: List[VerifierJudgement] = Field(default_factory=list)
    options: VerificationOptions = Field(default_factory=VerificationOptions)


class ProveRequest(BaseModel):
    """Stand-alone "prove this code is correct" request."""

    workspace: WorkspaceSpec
    function: str = Field(description="Qualified name, e.g. shop.pricing.apply_discount")
    call: str = Field(description="Call expression, e.g. apply_discount(price, pct)")
    post: str = Field(description="Post-condition over `result` and the domain variables")
    domain: str = Field(description="e.g. price = range(0, 101); pct = range(0, 101)")
    pre: Optional[str] = None
    allow: List[str] = Field(default_factory=list, description="Exception names that count as valid outcomes")
    setup: Optional[str] = None
