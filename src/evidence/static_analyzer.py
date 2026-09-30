"""Module 2, Steps 2.1–2.2 — deterministic static analysis orchestrator.

Heterogeneous sources are merged into one normalised ``StaticFinding`` list:

* ``trustcode-sast`` — built-in AST rule engine (always available)
* ``semgrep``        — ``semgrep scan --config auto --json`` when the CLI is installed
* ``bandit``         — ``bandit -r <workspace> -f json`` (HIGH/MEDIUM confidence only)

Subprocess failures never abort the pipeline; they are reported as tool status.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.settings import settings
from src.evidence.rules import fingerprint, scan_files
from src.models.evidence import SOP_LEVEL, StaticFinding

_SEMGREP_SEV = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}


@lru_cache(maxsize=1)
def tool_availability() -> Dict[str, bool]:
    return {
        "semgrep": shutil.which("semgrep") is not None,
        "bandit": importlib.util.find_spec("bandit") is not None or shutil.which("bandit") is not None,
        "sonar-scanner": shutil.which("sonar-scanner") is not None,
        "trivy": shutil.which("trivy") is not None,
        "pip-audit": shutil.which("pip-audit") is not None,
        "docker": shutil.which("docker") is not None,
        "git": shutil.which("git") is not None,
    }


def _line(files: Dict[str, str], rel: str, line: int) -> str:
    src = files.get(rel, "").splitlines()
    return src[line - 1].strip() if 0 < line <= len(src) else ""


def _rel(path: str, root: Path) -> str:
    p = Path(path)
    try:
        return (p if not p.is_absolute() else p.resolve().relative_to(root.resolve())).as_posix().lstrip("./")
    except ValueError:
        return p.as_posix()


def run_bandit(workdir: Path, files: Dict[str, str]) -> Tuple[List[StaticFinding], str]:
    if not settings.use_bandit or not tool_availability()["bandit"]:
        return [], "unavailable"
    cmd = [sys.executable, "-m", "bandit", "-r", ".", "-f", "json", "-q", "-x", "./.trustcode"]
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=settings.tool_timeout_s)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return [], f"error: {exc.__class__.__name__}"
    out: List[StaticFinding] = []
    occ: Dict[str, int] = {}
    for r in data.get("results", []):
        if r.get("issue_confidence") not in ("HIGH", "MEDIUM"):
            continue
        rel = _rel(r["filename"], workdir)
        is_test = "tests/" in rel or rel.split("/")[-1].startswith("test_")
        if is_test and r.get("test_id") in ("B101", "B105", "B106"):
            continue
        line = int(r.get("line_number", 1))
        code = _line(files, rel, line)
        sev = r.get("issue_severity", "LOW").upper()
        key = f"{r['test_id']}|{rel}|{code}"
        n = occ.get(key, 0)
        occ[key] = n + 1
        cwe = r.get("issue_cwe", {}).get("id")
        rng = r.get("line_range") or [line]
        out.append(
            StaticFinding(
                finding_id=f"bandit:{r['test_id']}:{rel}:{line}", tool="bandit", rule_id=r["test_id"], file=rel, line=line, end_line=max(rng),
                severity=sev if sev in ("LOW", "MEDIUM", "HIGH") else "LOW", sop_level=SOP_LEVEL.get(sev, "INFO"),  # type: ignore[arg-type]
                confidence=r.get("issue_confidence", "MEDIUM"), cwe=f"CWE-{cwe}" if cwe else None, category="Security", security=True,
                message=f"{r.get('test_name', '')}: {r.get('issue_text', '')}", code=code, fingerprint=fingerprint(r["test_id"], rel, code, None, n),
            )
        )
    return out, "ok"


def run_semgrep(workdir: Path, files: Dict[str, str]) -> Tuple[List[StaticFinding], str]:
    if not settings.use_semgrep or not tool_availability()["semgrep"]:
        return [], "unavailable"
    cmd = ["semgrep", "scan", "--config", settings.semgrep_config, "--json", "--quiet", "--exclude", ".trustcode"]
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True, timeout=settings.tool_timeout_s)
        data = json.loads(proc.stdout or "{}")
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        return [], f"error: {exc.__class__.__name__}"
    out: List[StaticFinding] = []
    for i, r in enumerate(data.get("results", [])):
        rel = _rel(r.get("path", ""), workdir)
        line = int(r.get("start", {}).get("line", 1))
        extra = r.get("extra", {})
        sev = _SEMGREP_SEV.get(str(extra.get("severity", "INFO")).upper(), "LOW")
        cwes = extra.get("metadata", {}).get("cwe") or []
        cwe = (cwes[0] if isinstance(cwes, list) and cwes else str(cwes or "")).split(":")[0] or None
        code = _line(files, rel, line)
        category = "Security" if cwe or "security" in r.get("check_id", "") else "Bug"
        out.append(
            StaticFinding(
                finding_id=f"semgrep:{i}:{rel}:{line}", tool="semgrep", rule_id=r.get("check_id", "semgrep"), file=rel, line=line,
                end_line=int(r.get("end", {}).get("line", line)), severity=sev, sop_level=SOP_LEVEL[sev], cwe=cwe,  # type: ignore[arg-type]
                category=category, security=category == "Security", message=extra.get("message", ""), code=code,
                fingerprint=fingerprint(r.get("check_id", ""), rel, code, None, 0),
            )
        )
    return out, "ok"


def analyse(workdir: Optional[Path], files: Dict[str, str], trees: Dict[str, ast.Module]) -> Tuple[List[StaticFinding], Dict[str, str]]:
    """Run every available analyser; returns findings + per-tool status."""
    status: Dict[str, str] = {}
    findings = scan_files(files, trees)
    status["trustcode-sast"] = "ok"
    if workdir is not None:
        for name, fn in (("bandit", run_bandit), ("semgrep", run_semgrep)):
            extra, st = fn(workdir, files)
            findings.extend(extra)
            status[name] = st
    findings.sort(key=lambda f: (f.file, f.line, f.tool, f.rule_id))
    return findings, status
