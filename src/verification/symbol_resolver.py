"""Module 3, Step 3.2 — deterministic symbol existence & scope verification.

Every token an AI cites is resolved against the AST symbol table with zero tolerance:
in-file definitions/usages, repository-wide definitions, qualified member access on
repository modules/classes, Python builtins, and *attributes of standard-library
modules* (verified by introspection, so ``hashlib.sha3_1024`` is caught). Anything
left unresolved is a hallucination, reported with nearest-neighbour suggestions.
"""
from __future__ import annotations

import builtins
import difflib
import importlib
from functools import lru_cache
from typing import List, Optional

from src.ingestion.ast_parser import PY_BUILTINS, STDLIB, SymbolIndex
from src.models.scoring import LocationCheck, SymbolMatch, SymbolResolution

_EXTERNAL_OK = {"self", "cls", "result", "args", "kwargs"}


@lru_cache(maxsize=512)
def stdlib_has(module: str, attr_chain: str) -> Optional[bool]:
    """Does ``module.attr_chain`` exist in the standard library? ``None`` = unknown."""
    root = module.split(".")[0]
    if root not in STDLIB:
        return None
    try:
        obj = importlib.import_module(module)
    except Exception:
        return None
    for part in attr_chain.split("."):
        if not part:
            continue
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


class SymbolResolver:
    def __init__(self, index: SymbolIndex) -> None:
        self.ix = index
        self._vocab = sorted(index.all_names())

    def suggest(self, token: str, n: int = 3) -> List[str]:
        last = token.split(".")[-1]
        return difflib.get_close_matches(last, self._vocab, n=n, cutoff=0.6)

    def _matches(self, syms) -> List[SymbolMatch]:
        return [SymbolMatch(qualified_name=s.qualified_name, kind=s.kind, file=s.file, line=s.line) for s in syms][:6]

    # ------------------------------------------------------------------ public API
    def resolve(self, token: str, target_file: str, role: str = "symbol", scope_hint: Optional[str] = None) -> SymbolResolution:
        ix = self.ix
        if role == "parameter":
            return self._resolve_parameter(token, target_file, scope_hint)
        parts = token.split(".")
        if len(parts) > 1:
            return self._resolve_dotted(token, parts, target_file)
        name = parts[0]
        defs = ix.lookup(name)
        in_file = [s for s in defs if s.file == target_file]
        if in_file:
            return SymbolResolution(token=token, status="resolved", matches=self._matches(in_file))
        if target_file and name in ix.names_in_file(target_file) and defs:
            return SymbolResolution(token=token, status="resolved", matches=self._matches(defs), note="used in target file, defined elsewhere")
        if defs:
            files = sorted({s.file for s in defs})
            return SymbolResolution(token=token, status="resolved_elsewhere", matches=self._matches(defs), note=f"exists only in {', '.join(files)} — not in {target_file or 'the cited file'}")
        if name in PY_BUILTINS or hasattr(builtins, name):
            return SymbolResolution(token=token, status="builtin", note="Python builtin")
        if name in STDLIB:
            return SymbolResolution(token=token, status="builtin", note="standard-library module")
        if name in _EXTERNAL_OK:
            return SymbolResolution(token=token, status="builtin", note="conventional name")
        if target_file and any(r.name.split(".")[0] == name for r in ix.refs_by_file.get(target_file, [])):
            return SymbolResolution(token=token, status="external", note="referenced in file (external/dynamic definition)")
        return SymbolResolution(token=token, status="hallucinated", suggestions=self.suggest(token), note=f"`{name}` is not defined, imported or referenced anywhere in the repository")

    def _resolve_parameter(self, token: str, target_file: str, scope_hint: Optional[str]) -> SymbolResolution:
        params = [s for s in self.ix.lookup(token) if s.kind == "parameter"]
        if scope_hint:
            scoped = [s for s in params if s.scope == scope_hint]
            if scoped:
                return SymbolResolution(token=token, role="parameter", status="resolved", matches=self._matches(scoped))
            fn = self.ix.get(scope_hint)
            real = [s.name for s in self.ix.by_file.get(fn.file if fn else target_file, []) if s.kind == "parameter" and s.scope == scope_hint]
            return SymbolResolution(token=token, role="parameter", status="hallucinated", suggestions=real[:4], note=f"`{scope_hint.split('.')[-1]}` has no parameter `{token}` (actual: {', '.join(real) or 'none'})")
        in_file = [s for s in params if s.file == target_file]
        if in_file:
            return SymbolResolution(token=token, role="parameter", status="resolved", matches=self._matches(in_file))
        return SymbolResolution(token=token, role="parameter", status="hallucinated", suggestions=self.suggest(token), note=f"no parameter named `{token}` in {target_file or 'repository'}")

    def _resolve_dotted(self, token: str, parts: List[str], target_file: str) -> SymbolResolution:
        ix = self.ix
        head, tail = parts[0], parts[1:]
        # 1. absolute qualified name
        if token in ix.symbols:
            return SymbolResolution(token=token, status="resolved", matches=self._matches([ix.symbols[token]]))
        # 2. head is an import alias / module / class in the repo
        bases: List[str] = []
        alias = ix.imports.get(target_file, {}).get(head)
        if alias:
            bases.append(alias)
        bases += [m for m in ix.modules if m == head or m.endswith("." + head)]
        bases += [s.qualified_name for s in ix.lookup(head) if s.kind in ("class", "module")]
        for base in bases:
            q = ".".join([base] + tail)
            if q in ix.symbols:
                return SymbolResolution(token=token, status="resolved", matches=self._matches([ix.symbols[q]]))
            if base in ix.modules or (ix.symbols.get(base) and ix.symbols[base].kind in ("class", "module")):
                sugg = [s.name for s in ix.symbols.values() if s.scope == base and s.kind in ("function", "method", "class", "global", "class_attribute", "attribute")]
                close = difflib.get_close_matches(tail[-1], sugg, n=3, cutoff=0.45) or sugg[:3]
                return SymbolResolution(token=token, status="hallucinated", suggestions=close, note=f"`{base}` exists but has no member `{'.'.join(tail)}`")
        # 3. standard-library module attribute
        std_base = alias or head
        verdict = stdlib_has(std_base, ".".join(tail))
        if verdict is True:
            return SymbolResolution(token=token, status="builtin", note="standard-library attribute (introspected)")
        if verdict is False:
            return SymbolResolution(token=token, status="hallucinated", note=f"standard-library module `{std_base}` has no attribute `{'.'.join(tail)}`")
        # 4. self.x / obj.method where only the head can be verified
        if head in ("self", "cls"):
            cands = [s for s in ix.lookup(tail[0]) if s.kind in ("method", "attribute", "class_attribute")]
            if cands:
                return SymbolResolution(token=token, status="resolved", matches=self._matches(cands))
            return SymbolResolution(token=token, status="hallucinated", suggestions=self.suggest(tail[0]), note=f"no attribute or method `{tail[0]}` on any class")
        head_res = self.resolve(head, target_file)
        if head_res.status in ("resolved", "external", "builtin"):
            return SymbolResolution(token=token, status="external", matches=head_res.matches, note=f"`{head}` verified; member `{'.'.join(tail)}` belongs to a runtime object")
        return SymbolResolution(token=token, status="hallucinated", suggestions=self.suggest(token), note=f"neither `{head}` nor `{token}` exists")

    # ------------------------------------------------------------------ locations
    def check_location(self, file: str, start: int, end: int, anchors: List[SymbolResolution]) -> LocationCheck:
        ix = self.ix
        if not file:
            return LocationCheck(file_exists=True, note="no file cited; location inferred from symbols")
        if file not in ix.sources:
            close = difflib.get_close_matches(file, list(ix.sources), n=1, cutoff=0.5)
            return LocationCheck(file_exists=False, note=f"file `{file}` does not exist in the workspace" + (f" (closest: {close[0]})" if close else ""))
        n = ix.file_line_count(file)
        if start < 1 or end > n:
            return LocationCheck(file_exists=True, file_lines=n, lines_valid=False, note=f"cited lines {start}-{end} are outside the file (1-{n})")
        loc = LocationCheck(file_exists=True, file_lines=n)
        best = None
        for a in anchors:
            for m in a.matches:
                if m.file != file or m.kind not in ("function", "method", "class"):
                    continue
                sym = ix.get(m.qualified_name)
                if sym is None:
                    continue
                if end < sym.line:
                    d = sym.line - end
                elif start > sym.end_line:
                    d = start - sym.end_line
                else:
                    d = 0
                if d and any(start - 3 <= r.line <= end + 3 for r in ix.refs_by_file.get(file, []) if r.name.split(".")[-1] == sym.name):
                    d = 0
                if best is None or d < best[0]:
                    best = (d, sym)
        if best is not None:
            loc.drift_lines, sym = best[0], best[1]
            loc.anchored_symbol = sym.qualified_name
            if loc.drift_lines > 6:
                loc.note = f"`{sym.name}` is defined at lines {sym.line}-{sym.end_line}, {loc.drift_lines} lines away from the cited range"
        return loc
