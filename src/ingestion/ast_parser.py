"""Module 1, Step 1.2 — polyglot symbol-table construction.

Python is parsed with the standard-library ``ast`` module (exact scopes, parameters,
imports and references). JavaScript/TypeScript/Java/Go use Tree-sitter when the
``tree_sitter_languages`` wheel is installed and fall back to a brace-matching lexer
otherwise, so the index is always populated.
"""
from __future__ import annotations

import ast
import builtins
import re
import sys
from pathlib import PurePosixPath
from typing import Dict, Iterable, List, Optional, Set, Tuple

from src.models.evidence import Reference, Symbol

try:  # optional polyglot backend
    from tree_sitter_languages import get_parser as _ts_get_parser  # type: ignore
except Exception:  # pragma: no cover - depends on optional wheel
    _ts_get_parser = None

LANG_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".go": "go",
}
CODE_EXTS = frozenset(LANG_BY_EXT)
PY_BUILTINS = frozenset(dir(builtins)) | {"__name__", "__file__", "__doc__", "__all__"}
STDLIB = frozenset(getattr(sys, "stdlib_module_names", ()))


def language_of(path: str) -> Optional[str]:
    return LANG_BY_EXT.get(PurePosixPath(path).suffix.lower())


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "__root__"


def dotted_name(node: ast.AST) -> Optional[str]:
    """``a.b.c`` for Name/Attribute chains, ``None`` for anything more dynamic."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        head = dotted_name(node.value)
        return f"{head}.{node.attr}" if head else None
    return None


class SymbolIndex:
    """In-memory symbol table shared by every downstream verifier."""

    def __init__(self) -> None:
        self.symbols: Dict[str, Symbol] = {}
        self.by_name: Dict[str, List[Symbol]] = {}
        self.by_file: Dict[str, List[Symbol]] = {}
        self.references: List[Reference] = []
        self.refs_by_file: Dict[str, List[Reference]] = {}
        self.imports: Dict[str, Dict[str, str]] = {}
        self.star_imports: Dict[str, List[str]] = {}
        self.modules: Dict[str, str] = {}
        self.trees: Dict[str, ast.Module] = {}
        self.nodes: Dict[str, ast.AST] = {}
        self.sources: Dict[str, str] = {}
        self.parse_errors: Dict[str, str] = {}
        self.backend: Dict[str, str] = {}
        self.extra_lines: Dict[str, List[int]] = {}

    # ------------------------------------------------------------------ building
    def add(self, sym: Symbol, node: Optional[ast.AST] = None) -> Symbol:
        existing = self.symbols.get(sym.qualified_name)
        if existing is not None:
            self.extra_lines.setdefault(sym.qualified_name, []).append(sym.line)
            return existing
        self.symbols[sym.qualified_name] = sym
        self.by_name.setdefault(sym.name, []).append(sym)
        self.by_file.setdefault(sym.file, []).append(sym)
        if node is not None:
            self.nodes[sym.qualified_name] = node
        return sym

    def add_ref(self, ref: Reference) -> None:
        self.references.append(ref)
        self.refs_by_file.setdefault(ref.file, []).append(ref)

    # ------------------------------------------------------------------ queries
    def lookup(self, name: str) -> List[Symbol]:
        return list(self.by_name.get(name, []))

    def get(self, qualified: str) -> Optional[Symbol]:
        return self.symbols.get(qualified)

    def files(self) -> List[str]:
        return sorted(self.sources)

    def file_line_count(self, file: str) -> int:
        src = self.sources.get(file)
        return 0 if src is None else len(src.splitlines())

    def functions(self) -> List[Symbol]:
        return [s for s in self.symbols.values() if s.kind in ("function", "method")]

    def enclosing(self, file: str, line: int, kinds: Tuple[str, ...] = ("function", "method")) -> Optional[Symbol]:
        best: Optional[Symbol] = None
        for s in self.by_file.get(file, []):
            if s.kind in kinds and s.line <= line <= s.end_line:
                if best is None or (s.end_line - s.line) < (best.end_line - best.line):
                    best = s
        return best

    def names_in_file(self, file: str) -> Set[str]:
        names = {s.name for s in self.by_file.get(file, [])}
        for r in self.refs_by_file.get(file, []):
            names.update(r.name.split("."))
        return names

    def all_names(self) -> Set[str]:
        return set(self.by_name)

    def references_to(self, name: str) -> List[Reference]:
        out = []
        for r in self.references:
            parts = r.name.split(".")
            if parts[-1] == name or r.name == name:
                out.append(r)
        return out

    def module_file(self, module: str) -> Optional[str]:
        return self.modules.get(module)

    def resolve_import_target(self, target: str) -> Optional[Symbol]:
        """Map an import target (``shop.pricing.apply_discount``) onto an indexed symbol."""
        if target in self.symbols:
            return self.symbols[target]
        return None

    # ------------------------------------------------------------------ UI export
    def to_tree(self) -> Dict[str, List[dict]]:
        tree: Dict[str, List[dict]] = {}
        for file in self.files():
            syms = sorted(self.by_file.get(file, []), key=lambda s: (s.line, s.qualified_name))
            nodes: Dict[str, dict] = {}
            roots: List[dict] = []
            for s in syms:
                if s.kind == "module":
                    continue
                item = {
                    "name": s.name,
                    "qualified": s.qualified_name,
                    "kind": s.kind,
                    "line": s.line,
                    "end_line": s.end_line,
                    "signature": s.signature,
                    "detail": s.detail,
                    "children": [],
                }
                nodes[s.qualified_name] = item
                parent = nodes.get(s.scope)
                (parent["children"] if parent else roots).append(item)
            tree[file] = roots
        return tree


# ====================================================================== Python
class _PyVisitor(ast.NodeVisitor):
    def __init__(self, ix: SymbolIndex, file: str, module: str) -> None:
        self.ix, self.file, self.module = ix, file, module
        self.scopes: List[str] = [module]
        self.kinds: List[str] = ["module"]
        ix.imports.setdefault(file, {})

    @property
    def scope(self) -> str:
        return self.scopes[-1]

    def _sym(self, name: str, kind: str, node: ast.AST, scope: Optional[str] = None, **kw) -> Symbol:
        sc = scope or self.scope
        line = getattr(node, "lineno", 1)
        return self.ix.add(
            Symbol(
                name=name,
                qualified_name=f"{sc}.{name}",
                kind=kind,
                file=self.file,
                line=line,
                end_line=getattr(node, "end_lineno", None) or line,
                scope=sc,
                **kw,
            ),
            node if kind in ("class", "function", "method") else None,
        )

    def _ref(self, name: str, node: ast.AST, ctx: str) -> None:
        self.ix.add_ref(Reference(name=name, file=self.file, line=getattr(node, "lineno", 0), scope=self.scope, ctx=ctx))

    def _push(self, name: str, kind: str) -> None:
        self.scopes.append(f"{self.scope}.{name}")
        self.kinds.append(kind)

    def _pop(self) -> None:
        self.scopes.pop()
        self.kinds.pop()

    def _class_scope(self) -> Optional[str]:
        for scope, kind in zip(reversed(self.scopes), reversed(self.kinds)):
            if kind == "class":
                return scope
        return None

    # -- definitions -------------------------------------------------------------
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(b) for b in node.bases]
        sig = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
        self._sym(node.name, "class", node, signature=sig)
        for d in node.decorator_list + node.bases:
            self.visit(d)
        self._push(node.name, "class")
        for stmt in node.body:
            self.visit(stmt)
        self._pop()

    def _visit_function(self, node) -> None:
        kind = "method" if self.kinds[-1] == "class" else "function"
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        sig = f"{prefix} {node.name}({ast.unparse(node.args)})"
        if node.returns is not None:
            sig += f" -> {ast.unparse(node.returns)}"
        self._sym(node.name, kind, node, signature=sig)
        for d in node.decorator_list:
            self.visit(d)
        for default in list(node.args.defaults) + [d for d in node.args.kw_defaults if d is not None]:
            self.visit(default)
        self._push(node.name, kind)
        a = node.args
        params = a.posonlyargs + a.args + a.kwonlyargs + ([a.vararg] if a.vararg else []) + ([a.kwarg] if a.kwarg else [])
        for p in params:
            self._sym(p.arg, "parameter", p, detail=ast.unparse(p.annotation) if p.annotation else None)
        for stmt in node.body:
            self.visit(stmt)
        self._pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _store(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            kind = {"module": "global", "class": "class_attribute"}.get(self.kinds[-1], "variable")
            self._sym(target.id, kind, target)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._store(elt)
        elif isinstance(target, ast.Starred):
            self._store(target.value)
        elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id in ("self", "cls"):
            cls_scope = self._class_scope()
            if cls_scope:
                self._sym(target.attr, "attribute", target, scope=cls_scope)
        else:
            self.visit(target)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for t in node.targets:
            self._store(t)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self.visit(node.value)
        self._store(node.target)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._ref(node.target.id, node.target, "load")
        self._store(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self.visit(node.value)
        self._store(node.target)

    def visit_For(self, node) -> None:
        self.visit(node.iter)
        self._store(node.target)
        for stmt in node.body + node.orelse:
            self.visit(stmt)

    visit_AsyncFor = visit_For

    def visit_With(self, node) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._store(item.optional_vars)
        for stmt in node.body:
            self.visit(stmt)

    visit_AsyncWith = visit_With

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.type is not None:
            self.visit(node.type)
        if node.name:
            self._sym(node.name, "variable", node)
        for stmt in node.body:
            self.visit(stmt)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit(node.iter)
        self._store(node.target)
        for cond in node.ifs:
            self.visit(cond)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.visit(node.body)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self._sym(local, "import", node, detail=alias.name)
            self.ix.imports[self.file][local] = target

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        if node.level:
            base = self.module.split(".")
            if not PurePosixPath(self.file).name == "__init__.py":
                base = base[:-1]
            base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
            mod = ".".join([p for p in base + ([mod] if mod else []) if p])
        for alias in node.names:
            if alias.name == "*":
                self.ix.star_imports.setdefault(self.file, []).append(mod)
                continue
            local = alias.asname or alias.name
            target = f"{mod}.{alias.name}" if mod else alias.name
            self._sym(local, "import", node, detail=target)
            self.ix.imports[self.file][local] = target

    # -- references -----------------------------------------------------------------
    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self._ref(node.id, node, "load")

    def visit_Attribute(self, node: ast.Attribute) -> None:
        dotted = dotted_name(node)
        if dotted and isinstance(node.ctx, ast.Load):
            self._ref(dotted, node, "attr")
        self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        dotted = dotted_name(node.func)
        if dotted:
            self._ref(dotted, node, "call")
        self.generic_visit(node)


def _index_python(ix: SymbolIndex, file: str, source: str) -> None:
    mod = module_name(file)
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError as exc:
        ix.parse_errors[file] = f"SyntaxError: {exc.msg} (line {exc.lineno})"
        ix.add(Symbol(name=mod.split(".")[-1], qualified_name=mod, kind="module", file=file, line=1, end_line=max(1, len(source.splitlines()))))
        return
    ix.trees[file] = tree
    ix.modules[mod] = file
    ix.backend[file] = "python-ast"
    ix.add(Symbol(name=mod.split(".")[-1], qualified_name=mod, kind="module", file=file, line=1, end_line=max(1, len(source.splitlines()))), tree)
    _PyVisitor(ix, file, mod).visit(tree)


# ====================================================================== other languages
_JS_KEYWORDS = {"if", "for", "while", "switch", "catch", "function", "return", "constructor", "else", "do", "try"}
_GENERIC_PATTERNS: Dict[str, List[Tuple[str, re.Pattern]]] = {
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(([^)]*)\)")),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\(([^)]*)\)|[A-Za-z_$][\w$]*)\s*=>")),
        ("method", re.compile(r"^\s+(?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\(([^)]*)\)\s*\{")),
    ],
    "java": [
        ("class", re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_]\w*)")),
        ("method", re.compile(r"^\s*(?:(?:public|private|protected|static|final|synchronized|abstract|default)\s+)*[\w<>\[\],.?\s]+?\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:throws\s+[\w.,\s]+)?\{")),
    ],
    "go": [
        ("class", re.compile(r"^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)")),
        ("function", re.compile(r"^func\s+(?:\(\s*\w+\s+\*?\w+\s*\)\s*)?([A-Za-z_]\w*)\s*\(([^)]*)\)")),
    ],
}
_GENERIC_PATTERNS["typescript"] = _GENERIC_PATTERNS["javascript"]
_GENERIC_PATTERNS["tsx"] = _GENERIC_PATTERNS["javascript"]
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_CALL = re.compile(r"([A-Za-z_$][\w$.]*)\s*\(")


def _block_end(lines: List[str], start: int) -> int:
    depth, seen = 0, False
    for i in range(start, len(lines)):
        code = re.sub(r"(\"[^\"]*\"|'[^']*'|`[^`]*`|//.*$)", "", lines[i])
        depth += code.count("{") - code.count("}")
        seen = seen or "{" in code
        if seen and depth <= 0:
            return i + 1
    return start + 1


def _ts_extract(language: str, source: str) -> Optional[List[Tuple[str, str, int, int, str]]]:
    if _ts_get_parser is None:
        return None
    try:
        parser = _ts_get_parser("typescript" if language == "tsx" else language)
    except Exception:
        return None
    tree = parser.parse(source.encode())
    wanted = {
        "function_declaration": "function",
        "method_definition": "method",
        "method_declaration": "method",
        "class_declaration": "class",
        "interface_declaration": "class",
        "type_spec": "class",
    }
    out: List[Tuple[str, str, int, int, str]] = []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        kind = wanted.get(n.type)
        if kind:
            name_node = n.child_by_field_name("name")
            if name_node is not None:
                params = n.child_by_field_name("parameters")
                out.append((kind, name_node.text.decode(), n.start_point[0] + 1, n.end_point[0] + 1, params.text.decode() if params else ""))
        stack.extend(reversed(n.children))
    return out


def _index_generic(ix: SymbolIndex, file: str, source: str, language: str) -> None:
    mod = module_name(file)
    lines = source.splitlines()
    ix.add(Symbol(name=mod.split(".")[-1], qualified_name=mod, kind="module", file=file, line=1, end_line=max(1, len(lines)), language=language))
    found = _ts_extract(language, source)
    ix.backend[file] = "tree-sitter" if found is not None else "lexical"
    if found is None:
        found = []
        for i, line in enumerate(lines):
            for kind, pat in _GENERIC_PATTERNS.get(language, []):
                m = pat.search(line)
                if m and m.group(1) not in _JS_KEYWORDS:
                    params = m.group(2) if m.lastindex and m.lastindex >= 2 and m.group(2) else ""
                    found.append((kind, m.group(1), i + 1, _block_end(lines, i), params))
                    break
    classes: List[Tuple[str, int, int]] = []
    for kind, name, start, end, params in sorted(found, key=lambda t: t[2]):
        scope = mod
        for cname, cs, ce in classes:
            if cs < start <= ce:
                scope = f"{mod}.{cname}"
                kind = "method" if kind in ("function", "method") else kind
        if kind == "method" and scope == mod:
            kind = "function"
        sym = ix.add(Symbol(name=name, qualified_name=f"{scope}.{name}", kind=kind, file=file, line=start, end_line=end, scope=scope, signature=f"{name}({params.strip()})" if kind != "class" else f"class {name}", language=language))
        if kind == "class":
            classes.append((name, start, end))
        for p in [x.strip() for x in params.split(",") if x.strip()]:
            pname = _IDENT.findall(p.split(":")[0].split("=")[0])
            if pname:
                ix.add(Symbol(name=pname[-1], qualified_name=f"{sym.qualified_name}.{pname[-1]}", kind="parameter", file=file, line=start, end_line=start, scope=sym.qualified_name, language=language))
    for i, line in enumerate(lines):
        for m in _CALL.finditer(line):
            ix.add_ref(Reference(name=m.group(1), file=file, line=i + 1, scope=mod, ctx="call"))
        for m in _IDENT.finditer(line):
            ix.add_ref(Reference(name=m.group(0), file=file, line=i + 1, scope=mod, ctx="load"))


# ====================================================================== entry point
def build_symbol_index(files: Dict[str, str]) -> SymbolIndex:
    ix = SymbolIndex()
    for path in sorted(files):
        language = language_of(path)
        if language is None:
            continue
        source = files[path]
        ix.sources[path] = source
        if language == "python":
            _index_python(ix, path, source)
        else:
            _index_generic(ix, path, source, language)
    return ix


def iter_python_functions(tree: ast.AST) -> Iterable[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
