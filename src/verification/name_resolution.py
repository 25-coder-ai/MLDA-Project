"""Post-patch symbol hallucination detector.

AI-generated fixes often call helpers that were never written (``sanitize_sql()``),
invent standard-library APIs (``hashlib.sha3_1024``), or pass keyword arguments that
the callee does not accept. This module computes, for a file, the set of

* undefined names (scope-aware, pyflakes-style),
* missing attributes on repository modules and standard-library modules, and
* unknown keyword arguments in calls to repository functions,

so the patch validator can diff *pre* vs *post* and attribute new ones to the patch.
"""
from __future__ import annotations

import ast
import difflib
from typing import Dict, List, Optional, Set, Tuple

from src.ingestion.ast_parser import PY_BUILTINS, SymbolIndex, dotted_name
from src.models.scoring import IntroducedSymbol
from src.verification.symbol_resolver import stdlib_has

Issue = Tuple[str, str, int, str]  # (kind, name, line, detail)


def _bound_names(node: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.Global, ast.Nonlocal)):
            names.update(n.names)
        elif isinstance(n, ast.MatchAs) and n.name:
            names.add(n.name)
    return names


def undefined_names(tree: ast.Module) -> List[Tuple[str, int]]:
    if any(isinstance(n, ast.ImportFrom) and any(a.name == "*" for a in n.names) for n in ast.walk(tree)):
        return []
    module_names = _bound_names(tree) | PY_BUILTINS
    out: List[Tuple[str, int]] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in module_names:
            out.append((n.id, n.lineno))
    return out


def _module_of(ix: SymbolIndex, file: str) -> Optional[str]:
    return next((m for m, f in ix.modules.items() if f == file), None)


def file_issues(file: str, tree: ast.Module, ix: SymbolIndex) -> List[Issue]:
    issues: List[Issue] = []
    for name, line in undefined_names(tree):
        issues.append(("undefined_name", name, line, f"`{name}` is not defined, imported or a builtin"))
    imports = ix.imports.get(file, {})
    for n in ast.walk(tree):
        if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load):
            dotted = dotted_name(n)
            if not dotted:
                continue
            head, *rest = dotted.split(".")
            target = imports.get(head)
            if not target or not rest:
                continue
            if target in ix.modules:
                q = f"{target}.{rest[0]}"
                if q not in ix.symbols:
                    issues.append(("missing_attribute", f"{head}.{rest[0]}", n.lineno, f"module `{target}` has no member `{rest[0]}`"))
            elif stdlib_has(target, rest[0]) is False:
                issues.append(("missing_attribute", f"{head}.{rest[0]}", n.lineno, f"standard-library module `{target}` has no attribute `{rest[0]}`"))
        elif isinstance(n, ast.Call) and n.keywords:
            callee = dotted_name(n.func)
            if not callee:
                continue
            q = _resolve_callee(ix, file, callee)
            node = ix.nodes.get(q) if q else None
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.args.kwarg is None:
                allowed = {a.arg for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs}
                for kw in n.keywords:
                    if kw.arg and kw.arg not in allowed:
                        issues.append(("unknown_keyword", f"{callee}({kw.arg}=)", n.lineno, f"`{callee.split('.')[-1]}` has no parameter `{kw.arg}` (accepts: {', '.join(sorted(allowed)) or 'none'})"))
    return issues


def _resolve_callee(ix: SymbolIndex, file: str, callee: str) -> Optional[str]:
    parts = callee.split(".")
    imports = ix.imports.get(file, {})
    mod = _module_of(ix, file)
    if parts[0] in imports:
        q = ".".join([imports[parts[0]]] + parts[1:])
        return q if q in ix.symbols else None
    if mod and f"{mod}.{callee}" in ix.symbols:
        return f"{mod}.{callee}"
    return None


def introduced(pre: List[Issue], post: List[Issue], file: str, vocab: List[str]) -> List[IntroducedSymbol]:
    before = {(k, n) for k, n, _, _ in pre}
    out: List[IntroducedSymbol] = []
    seen: Set[Tuple[str, str]] = set()
    for kind, name, line, detail in post:
        if (kind, name) in before or (kind, name) in seen:
            continue
        seen.add((kind, name))
        sugg = difflib.get_close_matches(name.split(".")[-1].split("(")[0], vocab, n=3, cutoff=0.6)
        out.append(IntroducedSymbol(file=file, line=line, name=name, kind=kind, detail=detail, suggestions=sugg))  # type: ignore[arg-type]
    return out
