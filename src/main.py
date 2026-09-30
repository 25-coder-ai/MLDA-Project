"""FastAPI entry point (Step 5.4): REST API + the TRUST-CODE web console.

    uvicorn src.main:app --reload        # then open http://127.0.0.1:8000
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config.settings import settings
from src.ingestion.ast_parser import build_symbol_index
from src.ingestion.git_loader import cleanup_session, load_workspace, materialise
from src.jobs import jobs
from src.models.evidence import ProofCertificate, ProofSpec
from src.models.ingestion import AIEvaluationPayload, AIReviewClaim, ProveRequest, WorkspaceSpec
from src.models.scoring import TrustReport
from src.pipeline import FAILURE_MODES, STAGES, TrustPipeline, capabilities
from src.reporting import to_markdown
from src.scenarios import get_scenario, list_scenarios
from src.verification.call_graph import CallGraph
from src.verification.claim_extractor import counterfactual, extract_entities, parse_review_text
from src.verification.prover import prove_adhoc

WEB = Path(__file__).parent / "web"

app = FastAPI(
    title="TRUST-CODE · LLM Trust Engine",
    version=settings.version,
    description="Evidence-based, deterministic verification of AI code reviews and AI-generated patches.",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=WEB), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


@app.get("/api/v1/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "capabilities": capabilities(), "stages": [{"key": k, "label": l} for k, l in STAGES], "failure_modes": [{"key": k, "label": l, "description": d} for k, l, d in FAILURE_MODES]}


@app.get("/api/v1/scenarios")
def scenarios() -> List[Dict[str, Any]]:
    return list_scenarios()


@app.get("/api/v1/scenarios/{scenario_id}")
def scenario(scenario_id: str) -> Dict[str, Any]:
    s = get_scenario(scenario_id)
    if s is None:
        raise HTTPException(404, f"unknown scenario {scenario_id!r}")
    return s


@app.post("/api/v1/verify", response_model=TrustReport)
def verify(payload: AIEvaluationPayload) -> TrustReport:
    """Synchronous end-to-end verification (CI/CD friendly)."""
    try:
        return TrustPipeline().run(payload)
    except FileNotFoundError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/v1/jobs")
def submit_job(payload: AIEvaluationPayload) -> Dict[str, str]:
    return {"job_id": jobs.submit(payload)}


@app.get("/api/v1/jobs/{job_id}")
def job_status(job_id: str, include_report: bool = True) -> Dict[str, Any]:
    job = jobs.get(job_id, include_report=include_report)
    if job is None:
        raise HTTPException(404, "unknown job")
    return job


class ParseRequest(BaseModel):
    review_text: str


@app.post("/api/v1/claims/parse")
def parse_claims(req: ParseRequest) -> Dict[str, Any]:
    claims: List[AIReviewClaim] = parse_review_text(req.review_text) if req.review_text.strip() else []
    return {
        "claims": [c.model_dump() for c in claims],
        "entities": {c.claim_id: extract_entities(c.claim_text).__dict__ for c in claims},
        "counterfactuals": {c.claim_id: counterfactual(c.claim_text) for c in claims},
    }


class FilesRequest(BaseModel):
    files: Dict[str, str]


@app.post("/api/v1/symbols")
def symbols(req: FilesRequest) -> Dict[str, Any]:
    ix = build_symbol_index(WorkspaceSpec(files=req.files).files)
    return {"tree": ix.to_tree(), "call_graph": CallGraph(ix).model().model_dump(), "parse_errors": ix.parse_errors, "backends": ix.backend}


@app.post("/api/v1/prove", response_model=ProofCertificate)
def prove(req: ProveRequest) -> ProofCertificate:
    """Discharge an ad-hoc correctness obligation in the sandbox."""
    snap = load_workspace(req.workspace)
    ix = build_symbol_index(snap.files)
    if ix.get(req.function) is None:
        raise HTTPException(422, f"`{req.function}` does not exist in the workspace — a proof cannot be about a hallucinated function")
    try:
        workdir = materialise(snap.files, settings.work_root / snap.session_id / "prove")
        spec = ProofSpec(expr=req.call, post=req.post or None, domain=req.domain, pre=req.pre, allow=req.allow)
        return prove_adhoc(workdir, req.function, spec, req.setup, ix)
    finally:
        cleanup_session(snap.session_id)


@app.post("/api/v1/report/markdown", response_class=PlainTextResponse)
def report_markdown(report: TrustReport) -> str:
    return to_markdown(report)
