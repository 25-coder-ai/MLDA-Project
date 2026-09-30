"""Sandbox tests for patch application, regression detection and the patch-level gates."""
import pytest

from src.scenarios.catalog import P_CENTS_NOVEL, P_HALLUCINATED_CONTEXT
from src.verification.diff_utils import DiffError, apply_patches, parse_unified_diff
from src.verification.mutation import _candidates, _mutate


def test_diff_applies_and_tracks_changed_lines(files):
    patches, warnings = parse_unified_diff(P_CENTS_NOVEL["suggested_diff"])
    out, app = apply_patches(files, patches)
    assert app.applied and not warnings
    assert app.changed_lines["shop/pricing.py"] == [29, 30, 31, 32]
    assert "cents // people / 100" in out["shop/pricing.py"]


def test_hallucinated_context_is_rejected(files):
    patches, _ = parse_unified_diff(P_HALLUCINATED_CONTEXT["suggested_diff"])
    _, app = apply_patches(files, patches)
    assert not app.applied and "context line not present" in app.error


def test_miscounted_hunk_headers_are_recomputed(files):
    diff = "--- a/shop/pricing.py\n+++ b/shop/pricing.py\n@@ -3,1 +3,99 @@\n-TAX_RATE = 0.08\n+TAX_RATE = 0.10\n"
    patches, warnings = parse_unified_diff(diff)
    _, app = apply_patches(files, patches)
    assert app.applied and warnings


def test_not_a_diff():
    with pytest.raises(DiffError):
        parse_unified_diff("hello world")


def test_mutation_operators_only_touch_changed_lines():
    import ast

    src = "def f(x):\n    if x < 0:\n        raise ValueError\n    return x + 1\n"
    cands = _candidates(ast.parse(src), {2})
    assert {c[1] for c in cands} >= {"relational-boundary", "negate-condition"}
    assert all(c[4] == 2 for c in cands)
    mutated = _mutate(src, next(c for c in cands if c[1] == "relational-boundary")[0], "relational-boundary")
    assert "x <= 0" in mutated


def test_bad_fixes_scenario_gates(run_scenario):
    r = run_scenario("bad-fixes")
    by = {p.patch.patch_id: p for p in r.patches}
    gates = lambda p: {g.gate for g in p.score.gates if g.triggered}  # noqa: E731
    assert {"Fatal Hallucination", "Test Regression Detected"} <= gates(by["P-HELPER"])
    assert by["P-HELPER"].introduced_symbols[0].name == "sanitize_sql"
    assert "tests/test_pricing.py::test_split_bill_rounding" in by["P-FLOOR"].tests.regressions
    assert "Security Vulnerability Injection" in gates(by["P-MD5"])
    # the auth bypass passes the whole test suite yet is rejected
    assert not by["P-ADMIN"].tests.regressions or all("test_" not in t for t in by["P-ADMIN"].tests.regressions)
    assert "Security Vulnerability Injection" in gates(by["P-ADMIN"])
    assert all(p.score.verdict == "REJECTED" for p in r.patches)


def test_inadequate_tests_and_novel_bug(run_scenario):
    cover = {p.patch.patch_id: p for p in run_scenario("tests-dont-cover").patches}
    assert cover["P-RES"].adequacy.verdict == "none" and cover["P-RES"].score.verdict != "TRUSTED"
    assert any(m.status == "survived" for m in cover["P-TOTAL"].adequacy.mutants)
    novel = run_scenario("novel-bugs").patches[0]
    assert not novel.tests.regressions and novel.tests.targets_satisfied
    assert novel.behavior.novel_bug_count > 0 and novel.score.verdict != "TRUSTED"


def test_good_patch_is_trusted(run_scenario):
    good = next(p for p in run_scenario("full-audit").patches if p.patch.patch_id == "P-SQL")
    assert good.score.verdict == "TRUSTED" and any(r.req_id == "REQ-002" and r.transition == "fixed" for r in good.requirements)
