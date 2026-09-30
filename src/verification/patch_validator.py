"""Module 4 — isolated fix & patch validation.

Pipeline per AI patch (each step runs against an ephemeral copy of the workspace):

 4.2  atomic application (strict context) + ``git apply --check`` cross-check
      + original-snippet grounding check
 4.3  functional & regression testing vs. the sealed baseline (zero-regression)
 4.4  differential security audit  ΔFindings = post − pre  (fingerprinted)
  +   post-patch symbol hallucination scan (undefined helpers / fake APIs / bad kwargs)
  +   requirement re-verification (checks, forbids, bounded proofs) before vs after
  +   test adequacy: changed-line coverage + mutation witness
  +   differential behaviour probing for novel bugs, and call-graph blast radius
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from config.settings import settings
from src.evidence.evidence_store import GroundTruthDatabase
from src.evidence.static_analyzer import analyse
from src.evidence.test_runner import run_suite
from src.ingestion.ast_parser import build_symbol_index, language_of
from src.ingestion.git_loader import materialise
from src.models.evidence import RequirementNode, StaticFinding
from src.models.ingestion import AIPatchPayload, VerificationOptions
from src.models.scoring import (
    BehaviorProbe,
    BlastRadius,
    ImpactedFunction,
    Mutant,
    PatchApplication,
    PatchVerdict,
    RequirementTransition,
    TestAdequacy,
    TestComparison,
    TestTransition,
)
from src.scoring.score_engine import compute
from src.verification.behavior_probe import probe_functions
from src.verification.call_graph import CallGraph
from src.verification.diff_utils import DiffError, apply_patches, git_apply_check, parse_unified_diff
from src.verification.mutation import _candidates, run_mutation
from src.verification.name_resolution import file_issues, introduced
from src.verification.requirement_verifier import evaluate_requirements

NOVEL = ("new_crash", "new_hang", "silent_value_change", "exception_changed")


def _norm_ws(s: str) -> str:
    return " ".join(s.split())


def _is_test_file(path: str) -> bool:
    name = path.split("/")[-1]
    return "tests/" in path or name.startswith("test_") or name.endswith("_test.py")


def _stmt_lines(tree: ast.AST) -> Set[int]:
    out: Set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.stmt) and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                continue  # docstring
            out.add(n.lineno)
    return out


def _finding_delta(a: List[StaticFinding], b: List[StaticFinding]) -> List[StaticFinding]:
    """Findings in ``a`` beyond those in ``b`` — multiset keyed by (tool, rule, file, function)."""
    budget: Dict[Tuple[str, str, str, str], int] = {}
    for f in b:
        k = (f.tool, f.rule_id, f.file, f.function or "")
        budget[k] = budget.get(k, 0) + 1
    out: List[StaticFinding] = []
    for f in a:
        k = (f.tool, f.rule_id, f.file, f.function or "")
        if budget.get(k, 0) > 0:
            budget[k] -= 1
        else:
            out.append(f)
    return out


class PatchValidator:
    def __init__(self, db: GroundTruthDatabase, graph: CallGraph, baseline_dir: Path, session_dir: Path, req_nodes: List[RequirementNode], options: VerificationOptions, log: Callable[[str], None] = lambda _m: None) -> None:
        self.db, self.graph = db, graph
        self.baseline_dir, self.session_dir = baseline_dir, session_dir
        self.req_nodes = req_nodes
        self.options = options
        self.log = log

    # ------------------------------------------------------------------ entry
    def validate(self, patch: AIPatchPayload, ordinal: int) -> PatchVerdict:
        files = self.db.files
        explanations: List[str] = []
        modes: Set[str] = set()

        # ---- 4.2 application ---------------------------------------------------
        snippet_found: Optional[bool] = None
        if patch.original_code_snippet.strip():
            snippet_found = _norm_ws(patch.original_code_snippet) in _norm_ws(files.get(patch.target_file, ""))
        try:
            parsed, warnings = parse_unified_diff(patch.suggested_diff, patch.target_file)
            patched, app = apply_patches(files, parsed)
            app.warnings = warnings
            app.git_check = git_apply_check(files, patch.suggested_diff, [p.old_path for p in parsed if p.old_path])
        except DiffError as exc:
            patched, app = files, PatchApplication(applied=False, error=f"Malformed diff: {exc}")
        app.snippet_found = snippet_found
        if not app.applied:
            modes.add("hallucinated_reference")
            modes.add("bad_fix")
            explanations.append(f"✗ Patch Application Failed — {app.error}.")
            if snippet_found is False:
                explanations.append("✗ The `original_code_snippet` the AI quoted does not exist in the target file.")
            score = compute({"hallucinated": 1.0 if snippet_found is False else 0.0}, [("Patch Application Failed", True, app.error or "")])
            return PatchVerdict(patch=patch, application=app, score=score, explanations=explanations, failure_modes=sorted(modes), evidence_counts={})

        counts = {"supported": 0.0, "unsupported": 0.0, "contradicted": 0.0, "hallucinated": 0.0}
        kinds: Set[str] = set()
        gates: List[Tuple[str, bool, str]] = []
        for w in app.warnings:
            explanations.append(f"• Diff hygiene: {w}.")
        if app.git_check and app.git_check != "ok":
            explanations.append(f"• `git apply --check` disagrees: {app.git_check}.")
        if snippet_found is False:
            counts["hallucinated"] += 1
            modes.add("hallucinated_reference")
            explanations.append("✗ The `original_code_snippet` quoted by the AI does not appear in the target file (hallucinated context).")
        for h in app.hunks:
            if h.status == "offset":
                explanations.append(f"• Hunk {h.header.split('@@')[1].strip()} applied at offset {h.offset:+d} — line numbers in the AI diff were wrong.")

        touched_py = [f for f in app.files_touched if language_of(f) == "python" and f in patched]
        primary = patch.target_file if patch.target_file in touched_py else next((f for f in touched_py if not _is_test_file(f)), None)
        verdict = PatchVerdict(patch=patch, application=app, patched_files={f: patched[f] for f in app.files_touched if f in patched}, score=compute({}))

        # ---- syntax + introduced symbols ---------------------------------------
        post_ix = build_symbol_index(patched)
        syntax_errors = {f: post_ix.parse_errors[f] for f in touched_py if f in post_ix.parse_errors}
        if syntax_errors:
            f, err = next(iter(syntax_errors.items()))
            verdict.syntax_ok, verdict.syntax_error = False, f"{f}: {err}"
            gates.append(("Syntax Error", True, verdict.syntax_error))
            explanations.append(f"✗ Patched code does not parse — {verdict.syntax_error}.")
            modes.add("bad_fix")
        vocab = sorted(self.db.index.all_names())
        for f in touched_py:
            if f in post_ix.trees:
                pre_issues = file_issues(f, self.db.index.trees[f], self.db.index) if f in self.db.index.trees else []
                verdict.introduced_symbols += introduced(pre_issues, file_issues(f, post_ix.trees[f], post_ix), f, vocab)
        if verdict.introduced_symbols:
            counts["hallucinated"] += len(verdict.introduced_symbols)
            names = ", ".join(f"`{s.name}`" for s in verdict.introduced_symbols[:4])
            gates.append(("Fatal Hallucination", True, f"patch references non-existent symbols: {names}"))
            modes.update({"fake_symbol", "bad_fix"})
            for s in verdict.introduced_symbols:
                hint = f" Did you mean {', '.join('`' + x + '`' for x in s.suggestions)}?" if s.suggestions else ""
                explanations.append(f"✗ Fix references a symbol that does not exist — {s.detail} (line {s.line}).{hint}")

        post_dir = materialise(patched, self.session_dir / f"patch-{ordinal}")

        # ---- 4.4 differential security audit ------------------------------------
        self.log(f"{patch.patch_id}: differential static analysis")
        post_findings, _ = analyse(post_dir, patched, post_ix.trees)
        for f in post_findings:
            enc = post_ix.enclosing(f.file, f.line)
            f.function = enc.qualified_name if enc else None
        verdict.static_new = _finding_delta(post_findings, self.db.findings)
        verdict.static_resolved = [f for f in _finding_delta(self.db.findings, post_findings) if f.file in app.files_touched]
        new_sec = [f for f in verdict.static_new if f.security and f.severity in ("HIGH", "MEDIUM")]
        if new_sec:
            gates.append(("Security Vulnerability Injection", True, "; ".join(f"{f.rule_id} {f.cwe or ''} at {f.file}:{f.line}" for f in new_sec[:3])))
            modes.add("bad_fix")
            for f in new_sec[:4]:
                explanations.append(f"✗ Security regressive: new {f.severity} {f.rule_id} ({f.cwe}) at {f.file}:{f.line} — {f.message}.")
        other_new = [f for f in verdict.static_new if f not in new_sec and f.severity != "INFO"]
        if other_new:
            modes.add("novel_bug")
            for f in other_new[:3]:
                explanations.append(f"△ New {f.category.lower()} finding introduced: {f.rule_id} at {f.file}:{f.line} — {f.message}.")
        if verdict.static_resolved:
            kinds.add("sast-resolved")
            explanations.append(f"✓ Resolves {len(verdict.static_resolved)} pre-existing finding(s): {', '.join(sorted({f.rule_id for f in verdict.static_resolved}))}.")

        # ---- 4.3 functional & regression testing ----------------------------------
        post_suite = None
        if self.options.run_tests and self.db.tests.ran:
            self.log(f"{patch.patch_id}: post-patch test suite")
            post_suite = run_suite(post_dir, trace=True, junit_name="post_patch_tests.xml")
            verdict.tests = self._compare_tests(patch, post_suite)
            t = verdict.tests
            if t.regressions:
                gates.append(("Test Regression Detected", True, ", ".join(t.regressions[:4])))
                modes.add("bad_fix")
                for tid in t.regressions[:5]:
                    msg = next((x.message for x in t.matrix if x.test_id == tid), "")
                    explanations.append(f"✗ CRITICAL REGRESSION: `{tid}` passed on baseline and fails after the patch. {self._first_error_line(msg)}")
            if t.targets_requested:
                if t.targets_unsatisfied:
                    counts["contradicted"] += 1
                    modes.add("requirement_mismatch")
                    explanations.append(f"✗ Fix does not achieve its stated goal: target test(s) still failing — {', '.join(t.targets_unsatisfied)}.")
                else:
                    kinds.add("target-tests")
                    explanations.append(f"✓ Defect resolution confirmed: {', '.join(t.targets_satisfied)} now pass.")
            elif t.fixed:
                kinds.add("target-tests")
                explanations.append(f"✓ Previously failing test(s) now pass: {', '.join(t.fixed[:3])}.")

        # ---- requirements before vs after ------------------------------------------
        exec_nodes = [n for n in self.req_nodes if n.executable]
        if exec_nodes:
            self.log(f"{patch.patch_id}: requirement re-verification")
            post_status = evaluate_requirements(exec_nodes, post_dir, post_findings, post_ix, run_proofs=self.options.run_proofs)
            changed_funcs = self._changed_functions(app, post_ix)
            for n in exec_nodes:
                before = self.db.requirements.get(n.req_id)
                after = post_status.get(n.req_id)
                if before is None or after is None:
                    continue
                linked = n.req_id in [r.upper() for r in patch.requirement_ids] or any(s in changed_funcs or any(c.endswith("." + s.split(".")[-1]) for c in changed_funcs) for s in n.symbols)
                b, a = before.status, after.status
                if b == "PASS" and a == "FAIL":
                    tr = "regression"
                elif b == "FAIL" and a == "PASS":
                    tr = "fixed"
                elif a == "FAIL":
                    tr = "still_failing"
                elif a == "PASS":
                    tr = "passing"
                else:
                    tr = "unverifiable"
                details = [f"{r.kind}: {r.expr} → {r.status}{(' (' + r.detail + ')') if r.detail and r.status != 'pass' else ''}" for r in after.results if r.status != "pass"][:4]
                verdict.requirements.append(RequirementTransition(req_id=n.req_id, title=n.title, before=b, after=a, transition=tr, linked=linked, details=details))  # type: ignore[arg-type]
                if linked:
                    verdict.proofs += after.proofs
                if tr == "regression":
                    gates.append(("Test Regression Detected", True, f"{n.req_id} acceptance checks passed before the patch and fail after"))
                    modes.update({"bad_fix", "requirement_mismatch"})
                    explanations.append(f"✗ Requirement regression: {n.req_id} ({n.title}) — {'; '.join(details) or 'now failing'}.")
                elif linked and a == "FAIL":
                    counts["contradicted"] += 1
                    modes.add("requirement_mismatch")
                    explanations.append(f"✗ Code does not satisfy {n.req_id} ({n.title}) after the patch — {'; '.join(details) or 'check failed'}.")
                    if any(c.status == "REFUTED" for c in after.proofs):
                        modes.add("unproven_correctness")
                elif linked and a == "PASS":
                    kinds.add("requirement")
                    explanations.append(f"✓ {n.req_id} ({n.title}) passes all executable acceptance clauses after the patch" + (" (was failing before)." if tr == "fixed" else "."))
                    if any(c.status == "PROVED_BOUNDED" for c in after.proofs):
                        kinds.add("proof")

        # ---- adequacy: coverage + mutation witness ------------------------------------
        if primary and primary in post_ix.trees and post_suite is not None and post_suite.ran:
            verdict.adequacy = self._adequacy(app, primary, post_ix.trees[primary], patched[primary], post_dir, post_suite)
            ad = verdict.adequacy
            if ad.verdict == "adequate":
                kinds.add("mutation-witness")
                ms_txt = f"mutation score {int(ad.mutation_score * 100)}%" if ad.mutation_score is not None else "no mutable operators on the changed lines"
                explanations.append(f"✓ Tests pin the change: {int((ad.coverage_ratio or 0) * 100)}% of changed lines executed, {ms_txt}.")
            elif ad.verdict in ("weak", "none"):
                counts["unsupported"] += 2 if ad.verdict == "none" else 1
                modes.add("inadequate_tests")
                explanations.append(f"△ Tests don't cover the issue — {ad.note}")

        # ---- behaviour probe ---------------------------------------------------------------
        if self.options.run_behavior_probe and primary:
            targets = []
            for q in self._changed_functions(app, post_ix):
                sym = post_ix.get(q)
                node = post_ix.nodes.get(q)
                if sym and sym.kind == "function" and q in self.db.index.symbols and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not _is_test_file(sym.file):
                    targets.append((q, q, node))
            if targets:
                self.log(f"{patch.patch_id}: differential behaviour probing ({len(targets)} function(s))")
                verdict.behavior = BehaviorProbe(functions=probe_functions(self.baseline_dir, post_dir, targets, post_ix.nodes))
                novel = [d for fp in verdict.behavior.functions for d in fp.divergences if d.kind in NOVEL]
                verdict.behavior.novel_bug_count = len(novel)
                verdict.behavior.intended_changes = sum(1 for fp in verdict.behavior.functions for d in fp.divergences if d.kind not in NOVEL)
                if novel:
                    counts["contradicted"] += min(3, len(novel))
                    modes.add("novel_bug")
                    d = novel[0]
                    explanations.append(f"✗ Novel bug: on input {d.input} the function {d.before.replace('→', 'returned')} before, but {d.after.replace('→', 'returns')} after the patch ({len(novel)} unexplained behaviour change(s); no test covers them).")

        # ---- blast radius ------------------------------------------------------------------
        changed = self._changed_functions(app, post_ix)
        radius = BlastRadius(changed_functions=sorted(changed))
        seen: Dict[str, int] = {}
        for q in changed:
            for caller, dist in self.graph.reverse_reach(q).items():
                if caller not in changed and (caller not in seen or dist < seen[caller]):
                    seen[caller] = dist
        for caller, dist in sorted(seen.items(), key=lambda kv: (kv[1], kv[0])):
            sym = self.db.index.get(caller)
            covered = bool(sym) and self.db.covered(sym.file, sym.line, sym.end_line)  # type: ignore[union-attr]
            radius.impacted.append(ImpactedFunction(qualified_name=caller, distance=dist, covered_by_tests=covered))
        verdict.blast_radius = radius
        uncovered = [i.qualified_name.split(".")[-1] for i in radius.impacted if not i.covered_by_tests and not i.qualified_name.startswith("tests")]
        if uncovered:
            explanations.append(f"• Blast radius: {len(radius.impacted)} caller(s) affected; untested: {', '.join('`' + u + '`' for u in uncovered[:4])}.")

        # ---- score -----------------------------------------------------------------------
        counts["supported"] = float(len(kinds))
        if not kinds:
            counts["unsupported"] += 1
            modes.add("unsupported_claim")
            explanations.append("△ No deterministic evidence shows that this patch fixes anything (no target test, requirement, resolved finding or mutation-adequate test).")
        gate_names = {g for g, t, _ in gates if t}
        for name in ("Test Regression Detected", "Security Vulnerability Injection", "Fatal Hallucination", "Syntax Error"):
            if name not in gate_names:
                gates.append((name, False, ""))
        # de-duplicate triggered gates with the same name
        merged: Dict[str, Tuple[str, bool, str]] = {}
        for g in gates:
            if g[0] not in merged or (g[1] and not merged[g[0]][1]):
                merged[g[0]] = g
            elif g[1] and merged[g[0]][1]:
                merged[g[0]] = (g[0], True, merged[g[0]][2] + "; " + g[2])
        verdict.score = compute(counts, list(merged.values()))
        verdict.evidence_counts = counts
        verdict.explanations = explanations
        verdict.failure_modes = sorted(modes)
        return verdict

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _first_error_line(msg: str) -> str:
        lines = [l.strip() for l in msg.splitlines() if l.strip().startswith("E ") or "Error" in l]
        return (lines[-1][:220] if lines else "").lstrip("E ").strip()

    def _changed_functions(self, app: PatchApplication, post_ix) -> Set[str]:
        out: Set[str] = set()
        for f, lines in app.changed_lines.items():
            for ln in lines:
                s = post_ix.enclosing(f, ln)
                if s:
                    out.add(s.qualified_name)
        for f, lines in app.removed_lines.items():
            for ln in lines:
                s = self.db.index.enclosing(f, ln)
                if s:
                    out.add(s.qualified_name)
        return out

    def _compare_tests(self, patch: AIPatchPayload, post) -> TestComparison:
        base = self.db.tests.by_id()
        after = post.by_id()
        comp = TestComparison(ran=post.ran, baseline=self.db.tests.counts(), post=post.counts(), post_suite=None)
        if not post.ran:
            comp.regressions = [tid for tid, t in base.items() if t.outcome == "passed"]
            return comp
        for tid in sorted(set(base) | set(after)):
            b = base[tid].outcome if tid in base else "missing"
            a = after[tid].outcome if tid in after else "missing"
            if b == "passed" and a in ("failed", "error", "missing"):
                tr = "regression" if a != "missing" else "removed"
            elif b in ("failed", "error") and a == "passed":
                tr = "fixed"
            elif b == "passed" and a == "passed":
                tr = "stable_pass"
            elif b == "missing" and a == "passed":
                tr = "new_pass"
            elif b == "missing":
                tr = "new_fail" if a in ("failed", "error") else "skipped"
            elif a == "skipped" or b == "skipped":
                tr = "skipped"
            else:
                tr = "stable_fail"
            msg = after[tid].message if tid in after else ""
            comp.matrix.append(TestTransition(test_id=tid, before=b, after=a, transition=tr, message=msg[-800:]))  # type: ignore[arg-type]
        # a removed test (e.g. its module no longer imports) is a regression too
        comp.regressions = [m.test_id for m in comp.matrix if m.transition in ("regression", "removed") and "<collection>" not in m.test_id]
        comp.regressions += [m.test_id for m in comp.matrix if m.transition == "new_fail" and m.test_id.endswith("::<collection>")]
        comp.fixed = [m.test_id for m in comp.matrix if m.transition == "fixed"]
        comp.targets_requested = list(patch.fixes_tests)
        for target in patch.fixes_tests:
            hits = [tid for tid in after if tid == target or tid.endswith("::" + target.split("::")[-1]) and target.split("::")[0] in tid]
            if hits and all(after[h].outcome == "passed" for h in hits):
                comp.targets_satisfied.append(target)
            else:
                comp.targets_unsatisfied.append(target)
        return comp

    def _adequacy(self, app: PatchApplication, file: str, tree: ast.AST, source: str, post_dir: Path, suite) -> TestAdequacy:
        changed = app.changed_lines.get(file, [])
        exec_lines = sorted(set(changed) & _stmt_lines(tree))
        ad = TestAdequacy(changed_executable_lines=exec_lines)
        if not exec_lines:
            ad.verdict = "not_applicable"
            ad.note = "the patch only removes code or changes non-executable lines"
            return ad
        covering: Set[str] = set()
        covered: Set[int] = set()
        module_level = {ln for ln in exec_lines if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno <= ln <= (n.end_lineno or n.lineno) for n in ast.walk(tree))}
        for t in suite.tests:
            hit = set(t.lines.get(file, [])) & set(exec_lines)
            if hit:
                covering.add(t.test_id)
                covered |= hit
            if module_level and t.lines.get(file):
                covered |= module_level  # executed when the module was imported for this test
        ad.covered_lines = sorted(covered)
        ad.covering_tests = sorted(covering)
        ad.coverage_ratio = round(len(covered) / len(exec_lines), 3)
        limit = self.options.max_mutants or settings.max_mutants
        if self.options.run_mutation:
            if covering:
                self.log(f"mutation witness on {file} ({len(exec_lines)} changed lines)")
                ad.mutants = run_mutation(post_dir, file, source, exec_lines, sorted(covering), limit)
            else:
                try:
                    cands = _candidates(ast.parse(source), set(exec_lines))[:limit]
                except SyntaxError:
                    cands = []
                ad.mutants = [Mutant(mutant_id=f"M{i + 1}", file=file, line=c[4], operator=c[1], original=c[2], mutated=c[3], status="survived") for i, c in enumerate(cands)]
        valid = [m for m in ad.mutants if m.status in ("killed", "survived", "timeout")]
        if valid:
            ad.mutation_score = round(sum(1 for m in valid if m.status != "survived") / len(valid), 3)
        cov = ad.coverage_ratio or 0.0
        ms = ad.mutation_score if ad.mutation_score is not None else (1.0 if cov >= 0.8 else 0.0)
        survivors = [m for m in ad.mutants if m.status == "survived"]
        if cov == 0:
            ad.verdict = "none"
            ad.note = f"no test executes any of the {len(exec_lines)} changed line(s) {exec_lines[:6]}; passing tests say nothing about this fix" + (f" ({len(survivors)} of {len(ad.mutants)} mutants survive)." if ad.mutants else ".")
        elif cov < 0.8 or ms < 0.6:
            ad.verdict = "weak"
            uncovered = sorted(set(exec_lines) - covered)
            parts = []
            if uncovered:
                parts.append(f"line(s) {uncovered[:6]} never executed")
            if survivors:
                s = survivors[0]
                parts.append(f"{len(survivors)}/{len(ad.mutants)} mutants survive (e.g. line {s.line}: `{s.original}` → `{s.mutated}` goes unnoticed)")
            ad.note = "; ".join(parts) + "."
        else:
            ad.verdict = "adequate"
            ad.note = "changed lines are executed and mutations are detected."
        return ad
