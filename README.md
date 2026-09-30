# TRUST-CODE — LLM Trust Engine

Evidence-based, deterministic verification of **AI code reviews** and **AI-generated patches**.
TRUST-CODE never asks another LLM whether an LLM was right. Every verdict is backed by
heterogeneous, reproducible evidence — AST symbol tables, static analysis, sandboxed
execution, executable specifications and bounded proofs — sealed in a tamper-evident ledger
*before* the AI's claims are even read.

Built to the *TRUST-CODE Complete SOP & Architecture* (Modules 1–5, folder manifest, trust-score
formula, hard gates and verdict routing), plus eight extensions described in
[`docs/INVENTION_DISCLOSURE.md`](docs/INVENTION_DISCLOSURE.md).

## Quick start

```bash
cd trust-code
pip install -r requirements.txt
python -m uvicorn src.main:app --port 8001      # web console → http://127.0.0.1:8001 (any free port works)
```

Pick a scenario in the **Workbench**, press **Run verification** (Ctrl+Enter), and explore the
**Report**, **Evidence** ledger, **Prove** console and **How it works** pages.

Other entry points:

| Interface | Command |
|---|---|
| REST API + OpenAPI docs | `http://127.0.0.1:8000/docs` |
| Streamlit dev console (SOP 5.4) | `streamlit run src/ui/dashboard.py` |
| CI / command line | `python -m src.cli scenario full-audit --md report.md` · `python -m src.cli verify --repo path --review review.txt --patch fix.diff --target pkg/mod.py` (exit code 1 when anything is REJECTED) |
| Tests | `python -m pytest` (46 tests, including every scenario end-to-end) |
| Docker (API + Celery + Redis + dashboard) | `docker compose up --build` |

Deep links: `/#/workbench?run=<scenario-id>` loads and runs a scenario; `/#/report?job=<job-id>` opens a finished job.

## What it detects

| Failure mode | How TRUST-CODE decides it |
|---|---|
| **Hallucinated / nonexistent code references** | Cited files, line ranges, modules, requirement IDs and patch context are resolved against the workspace; diffs whose context lines don't exist are rejected; quoted "original code" must be found verbatim. |
| **Fake / nonexistent symbols** | Zero-tolerance AST resolution of every cited function, class, variable and *parameter*; standard-library attributes are verified by introspection (`hashlib.sha3_1024` ✗); fixes are scanned post-patch for undefined helpers, missing attributes and unknown keyword arguments. Nearest-neighbour "did you mean" suggestions. |
| **Unsupported review claims** | Tri-state classification (SPEC 3.4). A claim with no corroborating AST fact, static finding, test, requirement or proof is UNSUPPORTED; impact claims without a call path are "Unsupported Impact Claims" (NetworkX). |
| **Semantic hallucinations** | Claims are compiled into polarity-aware predicates (initialised-before-use via definite-assignment analysis, handles-exceptions, None-guards, parameterised SQL, recursion, return kinds, constant-time compare, …) and decided on the AST. Passing tests that exercise exactly the claimed behaviour also contradict it. Location drift is detected. |
| **Code vs requirement mismatch** | Requirements carry executable `check:` / `forbid:` / `prove:` clauses. Compliance claims are checked against them; patches are re-verified before/after (fixed / still failing / regression). |
| **AI fix introduces another bug** | Sandboxed baseline vs post-patch test matrix (zero-regression gate), differential SAST ΔFindings (new CWE gate), requirement regressions, syntax and phantom-symbol gates. |
| **Tests don't cover the issue** | Per-test line tracing gives changed-line coverage; a *mutation witness* mutates only the patched lines and reports survivors. |
| **LLM verifier makes the same mistake** | Given an LLM-judge transcript, the judge is scored against ground truth: same-mistake rate, φ-correlation between generator and judge errors, independence index = 1 − φ, shared hallucinated symbols. |
| **LLM confirmation bias** | The engine generates a negated *counterfactual* of every claim; a judge that confirms both is polarity-insensitive. Rationales that restate the claim without evidence anchors are flagged. |
| **Unknown / novel bugs** | Differential behaviour probing synthesises inputs from each changed function and diffs pre/post behaviour: new crashes, hangs and silent value changes nobody reported. |
| **"Prove this code is correct"** | Correctness claims become proof obligations discharged exhaustively over a finite domain (PROVED_BOUNDED), refuted with a counterexample, or left UNPROVEN. Universal claims backed only by bounded proofs are capped at UNCERTAIN. Proofs about hallucinated functions are refused. |

## Scoring (SOP Module 5)

`S = clamp(0, 100, 50 + 25·C_supported − 15·C_unsupported − 35·C_contradicted − 50·C_hallucinated)`
computed as a NumPy dot product. `C_supported` counts *distinct evidence kinds* (e.g. `sast:bandit`,
`sast:trustcode-sast`, `ast-predicate`, `requirement`, `proof`, `dynamic-test`, `mutation-witness`).
Hard gates force `S = 0`: test/requirement regression, new HIGH/MEDIUM CWE, fatal hallucination
(any cited symbol missing), syntax error, patch application failure.
Routing: **TRUSTED ≥ 80**, **UNCERTAIN ≥ 50**, else **REJECTED**. Weights and bands are configurable (`.env.example`).

## Architecture

```
Input ─► Ingestion (git_loader · ast_parser · spec_indexer)
      ─► Evidence (static_analyzer[trustcode-sast, Bandit, Semgrep] · test_runner · requirement_verifier · call_graph)
      ─► SEAL (ledger.py: SHA-256 chain, sealed before claims are parsed)
      ─► Verification  A: claim_extractor → symbol_resolver → semantic_checker → claim_verifier
                       B: patch_validator → diff_utils · name_resolution · mutation · behavior_probe · prover
                       C: verifier_audit (LLM-judge independence & bias)
      ─► Scoring (weights · score_engine) ─► FastAPI · web console · Streamlit · CLI · Markdown/JSON
```

`src/sandbox/harness.py` is copied into every ephemeral workspace and run as a separate process
(scrubbed environment, timeouts) or — with `TRUSTCODE_SANDBOX_BACKEND=docker` — inside a
network-less, read-only, non-root container with optional gVisor (`TRUSTCODE_DOCKER_RUNTIME=runsc`).

## Optional components

Everything degrades gracefully and the console shows what is active (click the status pill):
Semgrep, Tree-sitter, spaCy, FAISS/LangChain splitters, Docker, Celery/Redis, Trivy/pip-audit.
Python projects get full depth; JS/TS/Java/Go get symbol resolution and lexical SAST.

## Honest limits

* The **local** sandbox backend isolates by process and directory only — use the Docker/gVisor backend for untrusted code.
* Bounded proofs cover only their declared finite domain; certificates say so explicitly.
* Semantic predicates cover a fixed catalogue of claim types; anything outside it is reported as undecidable rather than guessed.
* Behaviour-probe divergences are flagged for review: TRUST-CODE cannot know whether a value change was intended.
