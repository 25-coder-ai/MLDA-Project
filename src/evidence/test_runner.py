"""Module 2, Step 2.3 — baseline dynamic test execution.

Runs the suite inside the sandbox via the harness (pytest when importable, otherwise
the built-in runner), writes JUnit XML, and records per-test executed lines so later
stages can reason about *which* tests actually exercise a given change.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from src.models.evidence import TestCaseResult, TestSuiteResult
from src.sandbox.runner import run_harness


def has_tests(files: List[str]) -> bool:
    return any(f.endswith(".py") and (f.split("/")[-1].startswith("test_") or f.endswith("_test.py")) for f in files)


def run_suite(workdir: Path, *, trace: bool = True, fail_fast: bool = False, selection: Optional[List[str]] = None, junit_name: Optional[str] = None) -> TestSuiteResult:
    payload = {"trace": trace, "fail_fast": fail_fast, "selection": selection or None}
    if junit_name:
        payload["junit"] = str((workdir / ".trustcode" / junit_name).resolve())
    raw = run_harness(workdir, "tests", payload)
    suite = TestSuiteResult(ran=True, duration_ms=raw.get("_elapsed_ms", 0), junit_xml=payload.get("junit"))
    if raw.get("_error") or raw.get("fatal"):
        suite.error = raw.get("_error") or raw.get("fatal")
        suite.ran = False
        return suite
    suite.framework = raw.get("framework", "unknown")
    for t in raw.get("tests", []):
        suite.tests.append(TestCaseResult(test_id=t["test_id"].replace("\\", "/"), outcome=t.get("outcome", "error") if t.get("outcome") in ("passed", "failed", "error", "skipped") else "error", message=t.get("message", ""), duration=t.get("duration", 0.0), lines=t.get("lines", {})))
    suite.collection_errors = raw.get("collection_errors", [])
    for err in suite.collection_errors:
        suite.tests.append(TestCaseResult(test_id=f"{err['id']}::<collection>", outcome="error", message=err.get("message", "")))
    suite.tests.sort(key=lambda t: t.test_id)
    return suite
