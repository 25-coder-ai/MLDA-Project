"""Differential behaviour probing — surfacing *unknown / novel* bugs.

Tests only check what someone thought to check. For every function a patch touches,
TRUST-CODE synthesises inputs from the function itself (annotations, defaults,
parameter-name semantics, literals and dictionary keys the body uses, plus boundary
neighbours), executes the pre- and post-patch versions side by side in the sandbox,
and classifies every divergence:

* ``new_crash`` / ``new_hang``   — post raises / hangs where pre returned  → novel bug
* ``silent_value_change``        — both return, results differ             → novel bug candidate
* ``exception_changed``          — both raise, different exception types
* ``crash_fixed``                — pre raised, post returns / raises the intended error
"""
from __future__ import annotations

import ast
import itertools
import random
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.settings import settings
from src.models.scoring import Divergence, FunctionProbe
from src.sandbox.runner import run_harness

_RESOURCE = re.compile(r"^(conn|connection|db|cursor|session|client|request|req|sock|socket|file|fh|fp|path|url|engine|app)$", re.I)
_MONEY = [0, 1, 10, 100, 19.99, 0.29, 1e6, -5]
_INT = [0, 1, 2, 3, 7, -1, 10, 100]
_STR = ["", "a", " 7 ", "0", "-3", "abc", "1,200", "x' OR '1'='1"]
_NAME_POOLS = [
    (re.compile(r"(price|amount|total|cost|balance|money|fee|subtotal|value)", re.I), _MONEY),
    (re.compile(r"^(?:qty|quantity|count|num|n|people|size|limit|index|idx|pct|percent|rate|threshold|age|days|k|i|j)$|_(?:qty|count|rate|pct|size|limit|num)$", re.I), _INT),
    (re.compile(r"^(?:name|text|raw|s|string|sku|code|username|password|label|key|query|msg)$|_(?:name|text|str|code|key)$", re.I), _STR),
    (re.compile(r"^(?:items|rows|values|xs|lst|list|entries|records|orders|lines)$", re.I), None),
    (re.compile(r"^(?:stock|mapping|data|config|cache|d|dict|inventory|counts)$", re.I), None),
    (re.compile(r"^(?:flag|enabled|strict|force|verbose)$", re.I), [True, False]),
]


def _dict_keys(func: ast.AST, param: str) -> List[str]:
    keys = []
    loop_vars = {n.target.id for n in ast.walk(func) if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == param and isinstance(n.target, ast.Name)}
    for n in ast.walk(func):
        if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str):
            if isinstance(n.value, ast.Name) and (n.value.id == param or n.value.id in loop_vars):
                keys.append(n.slice.value)
    return sorted(set(keys))


def _called_with_item_keys(func: ast.AST, param: str, index_nodes: Dict[str, ast.AST]) -> List[str]:
    """Keys used by helpers the loop variable is passed to (e.g. ``line_total(item)``)."""
    keys: List[str] = []
    loop_vars = {n.target.id for n in ast.walk(func) if isinstance(n, ast.For) and isinstance(n.iter, ast.Name) and n.iter.id == param and isinstance(n.target, ast.Name)}
    for n in ast.walk(func):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and any(isinstance(a, ast.Name) and a.id in loop_vars for a in n.args):
            helper = next((v for k, v in index_nodes.items() if k.endswith("." + n.func.id)), None)
            if isinstance(helper, (ast.FunctionDef, ast.AsyncFunctionDef)) and helper.args.args:
                keys += _dict_keys(helper, helper.args.args[0].arg)
    return sorted(set(keys))


def _literals(func: ast.AST) -> List[object]:
    vals = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool) and abs(n.value) < 10**6:
            vals.update({n.value, n.value - 1, n.value + 1})
    return sorted(vals, key=lambda v: (abs(v), v))[:8]


def _pool(func: ast.AST, arg: ast.arg, default: Optional[ast.AST], nodes: Dict[str, ast.AST]) -> Optional[List[object]]:
    name = arg.arg
    if _RESOURCE.match(name):
        return None
    ann = ast.unparse(arg.annotation) if arg.annotation is not None else ""
    lits = _literals(func)
    if "int" in ann:
        return sorted(set(_INT + [v for v in lits if isinstance(v, int)]))[:10]
    if "float" in ann:
        return _MONEY
    if "str" in ann:
        return _STR
    if "bool" in ann:
        return [True, False]
    if default is not None and isinstance(default, ast.Constant):
        base = {bool: [True, False], int: _INT, float: [0.0, 0.05, 0.08, 0.2, 1.0, -0.1], str: _STR}.get(type(default.value))
        if base is not None:
            return list(dict.fromkeys([default.value] + base))
        if default.value is None:
            return [None, 0, 1, "x"]
    for pat, pool in _NAME_POOLS:
        if pat.search(name):
            if pool is not None:
                return list(pool)
            keys = sorted(set(_dict_keys(func, name)) | set(_called_with_item_keys(func, name, nodes)))
            if "items" in pat.pattern:
                if keys:
                    one = {k: 2 for k in keys}
                    return [[], [one], [one, {k: 0 for k in keys}], [{k: -1 for k in keys}], [{k: 1.5 for k in keys}]]
                return [[], [1], [1, 2, 3], [0], [-1]]
            return [{}, {"A": 5}, {"A": 0}, {"A": 5, "B": 1}]
    return list(dict.fromkeys(lits + [0, 1, -1, "", None]))[:8]


def generate_cases(func: ast.AST, nodes: Dict[str, ast.AST]) -> Tuple[List[str], Optional[str]]:
    a = func.args  # type: ignore[attr-defined]
    params = a.posonlyargs + a.args
    if params and params[0].arg in ("self", "cls"):
        return [], "method (requires an instance)"
    defaults: List[Optional[ast.AST]] = [None] * (len(params) - len(a.defaults)) + list(a.defaults)
    pools = []
    for p, d in zip(params, defaults):
        pool = _pool(func, p, d, nodes)
        if pool is None:
            return [], f"parameter `{p.arg}` needs an external resource"
        pools.append(pool)
    combos = list(itertools.product(*pools)) if pools else [()]
    limit = settings.max_probe_cases
    if len(combos) > limit:
        rng = random.Random(7)
        combos = combos[:8] + rng.sample(combos[8:], limit - 8)
    return [repr(tuple(c)) for c in combos], None


def _describe(rec: Dict) -> str:
    if rec.get("timeout"):
        return "⏱ hang (> timeout)"
    if "exc" in rec:
        return f"raises {rec['exc']}" + (f": {rec.get('msg')}" if rec.get("msg") else "")
    ok = str(rec.get("ok"))
    return f"→ {ok if len(ok) <= 48 else ok[:45] + '…'}"


def _explicit_raises(func: ast.AST) -> set:
    out = set()
    for n in ast.walk(func):
        if isinstance(n, ast.Raise) and n.exc is not None:
            target = n.exc.func if isinstance(n.exc, ast.Call) else n.exc
            if isinstance(target, ast.Name):
                out.add(target.id)
    return out


def probe_functions(pre_dir: Path, post_dir: Path, targets: List[Tuple[str, str, ast.AST]], nodes: Dict[str, ast.AST]) -> List[FunctionProbe]:
    """``targets``: (qualified_name, importable dotted path, post-patch FunctionDef)."""
    probes: List[FunctionProbe] = []
    tasks = []
    for q, dotted, fnode in targets:
        cases, skip = generate_cases(fnode, nodes)
        probe = FunctionProbe(qualified_name=q, cases_tried=len(cases), skipped_reason=skip)
        probes.append(probe)
        if cases:
            tasks.append({"id": q, "kind": "probe", "target": dotted, "cases": cases, "budget_s": 20})
    if not tasks:
        return probes
    payload = {"tasks": tasks, "call_timeout": settings.probe_call_timeout_s}
    pre = run_harness(pre_dir, "tasks", payload).get("results", {})
    post = run_harness(post_dir, "tasks", payload).get("results", {})
    raises_by_q = {q: _explicit_raises(f) for q, _, f in targets}
    for probe in probes:
        deliberate = raises_by_q.get(probe.qualified_name, set())
        a = {r["input"]: r for r in pre.get(probe.qualified_name, {}).get("results", [])}
        b_raw = post.get(probe.qualified_name, {})
        if b_raw.get("status") == "error":
            probe.skipped_reason = f"post-patch import failed: {b_raw.get('detail', '')[:120]}"
            continue
        for rec in b_raw.get("results", []):
            before = a.get(rec["input"])
            if before is None:
                if rec.get("timeout"):
                    probe.divergences.append(Divergence(input=rec["input"], before="(not reached)", after=_describe(rec), kind="new_hang"))
                continue
            if before.get("exc") == "TypeError" and pre.get(probe.qualified_name, {}).get("status") == "error":
                continue
            kind = None
            if rec.get("timeout") and not before.get("timeout"):
                kind = "new_hang"
            elif "exc" in rec and "ok" in before:
                kind = "validation_added" if rec["exc"] in deliberate else "new_crash"
            elif "ok" in rec and "exc" in before:
                kind = "crash_fixed"
            elif "exc" in rec and "exc" in before and rec["exc"] != before["exc"]:
                kind = "crash_fixed" if rec["exc"] in deliberate else "exception_changed"
            elif "ok" in rec and "ok" in before and rec["ok"] != before["ok"]:
                same_num = "num" in rec and "num" in before and abs(rec["num"] - before["num"]) <= 1e-9 * max(1.0, abs(before["num"]))
                kind = None if same_num else "silent_value_change"
            if kind:
                probe.divergences.append(Divergence(input=rec["input"], before=_describe(before), after=_describe(rec), kind=kind))  # type: ignore[arg-type]
    return probes
