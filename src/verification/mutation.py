"""Mutation witness — do the tests actually pin down the patched behaviour?

"All tests pass" is only meaningful if the tests would *fail* when the patched logic
is wrong. Small semantic mutations (operator swaps, boundary shifts, negated
conditions, constant nudges) are injected **only on the lines the patch changed**;
each mutant runs against the suite in its own sandbox. Surviving mutants are
concrete proof that the tests do not cover the issue the patch claims to fix.
"""
from __future__ import annotations

import ast
import copy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from config.settings import settings
from src.evidence.test_runner import run_suite
from src.models.scoring import Mutant
from src.sandbox.runner import clone_dir

_CMP_SWAP = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_BIN_SWAP = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult, ast.FloorDiv: ast.Div}
_SYM = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=", ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.FloorDiv: "//"}


def _candidates(tree: ast.Module, lines: Set[int]) -> List[Tuple[int, str, str, str, int]]:
    """(node_index, operator, original, mutated, line) for nodes on changed lines."""
    out = []
    for idx, node in enumerate(ast.walk(tree)):
        line = getattr(node, "lineno", None)
        if line not in lines:
            continue
        if isinstance(node, ast.Compare) and type(node.ops[0]) in _CMP_SWAP:
            op = type(node.ops[0])
            out.append((idx, "relational-boundary", _SYM[op], _SYM[_CMP_SWAP[op]], line))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN_SWAP:
            op = type(node.op)
            out.append((idx, "arithmetic-swap", _SYM[op], _SYM[_BIN_SWAP[op]], line))
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            out.append((idx, "constant-nudge", repr(node.value), repr(node.value + 1), line))
        elif isinstance(node, ast.If):
            out.append((idx, "negate-condition", ast.unparse(node.test)[:40], f"not ({ast.unparse(node.test)[:34]})", line))
        elif isinstance(node, ast.BoolOp):
            out.append((idx, "logical-swap", "and" if isinstance(node.op, ast.And) else "or", "or" if isinstance(node.op, ast.And) else "and", line))
        elif isinstance(node, ast.Raise):
            out.append((idx, "remove-raise", "raise …", "pass", line))
    return out


def _mutate(source: str, idx: int, operator: str) -> Optional[str]:
    tree = ast.parse(source)
    target = next((n for i, n in enumerate(ast.walk(tree)) if i == idx), None)
    if target is None:
        return None
    if operator == "relational-boundary":
        target.ops[0] = _CMP_SWAP[type(target.ops[0])]()  # type: ignore[attr-defined]
    elif operator == "arithmetic-swap":
        target.op = _BIN_SWAP[type(target.op)]()  # type: ignore[attr-defined]
    elif operator == "constant-nudge":
        target.value = target.value + 1  # type: ignore[attr-defined]
    elif operator == "negate-condition":
        target.test = ast.UnaryOp(op=ast.Not(), operand=target.test)  # type: ignore[attr-defined]
    elif operator == "logical-swap":
        target.op = ast.Or() if isinstance(target.op, ast.And) else ast.And()  # type: ignore[attr-defined]
    elif operator == "remove-raise":
        parent = next((p for p in ast.walk(tree) for f in ("body", "orelse", "finalbody") if target in getattr(p, f, []) or []), None)
        if parent is None:
            return None
        for f in ("body", "orelse", "finalbody"):
            block = getattr(parent, f, None)
            if isinstance(block, list) and target in block:
                block[block.index(target)] = ast.Pass()
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def run_mutation(workdir: Path, file: str, source: str, changed: List[int], selection: Optional[List[str]], limit: int) -> List[Mutant]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    cands = _candidates(tree, set(changed))
    # prefer operator diversity: round-robin across operator kinds
    by_op: Dict[str, List] = {}
    for c in cands:
        by_op.setdefault(c[1], []).append(c)
    picked: List = []
    while len(picked) < limit and any(by_op.values()):
        for op in list(by_op):
            if by_op[op] and len(picked) < limit:
                picked.append(by_op[op].pop(0))
    if not picked:
        return []

    def run_one(i: int, cand) -> Mutant:
        idx, operator, original, mutated, line = cand
        m = Mutant(mutant_id=f"M{i + 1}", file=file, line=line, operator=operator, original=original, mutated=mutated, status="error")
        new_src = _mutate(source, idx, operator)
        if new_src is None:
            return m
        mdir = clone_dir(workdir, workdir.parent / f"{workdir.name}-mut{i}")
        try:
            (mdir / file).write_text(new_src, encoding="utf-8", newline="\n")
            suite = run_suite(mdir, trace=False, fail_fast=True, selection=selection)
            if suite.error:
                m.status = "timeout" if "timeout" in suite.error else "error"
                return m
            failing = [t.test_id for t in suite.tests if t.outcome in ("failed", "error")]
            m.status = "killed" if failing else "survived"
            m.killed_by = failing[:3]
        finally:
            import shutil

            shutil.rmtree(mdir, ignore_errors=True)
        return m

    with ThreadPoolExecutor(max_workers=max(1, settings.parallelism)) as pool:
        return list(pool.map(lambda p: run_one(*p), enumerate(picked)))
