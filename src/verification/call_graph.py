"""Module 3, Step 3.3 — call-graph & flow mapping (NetworkX).

``G = (V, E)``: vertices are modules/classes/functions, edges are resolved invocations
(``call``) and import dependencies (``import``). Used for impact-claim validation
(does a path C → A exist?), recursion detection, dead-code claims and the patch
blast radius (reverse reachability from changed functions).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import networkx as nx

from src.ingestion.ast_parser import SymbolIndex
from src.models.evidence import CallGraphEdge, CallGraphModel, CallGraphNode

_CALLABLE = ("function", "method", "class")


class CallGraph:
    def __init__(self, index: SymbolIndex) -> None:
        self.ix = index
        self.g = nx.DiGraph()
        self._build()

    # ------------------------------------------------------------------ build
    def _resolve(self, name: str, file: str, scope: str) -> Optional[str]:
        ix = self.ix
        parts = name.split(".")
        head, rest = parts[0], parts[1:]
        module = next((m for m, f in ix.modules.items() if f == file), None)
        if head in ("self", "cls") and rest:
            cls = self._class_of(scope)
            if cls and f"{cls}.{rest[0]}" in ix.symbols:
                return f"{cls}.{rest[0]}"
            return None
        # nested scopes outward
        s = scope
        while s:
            cand = f"{s}.{head}"
            sym = ix.symbols.get(cand)
            if sym is not None and sym.kind in _CALLABLE + ("import",):
                if sym.kind == "import":
                    return self._follow(ix.imports.get(file, {}).get(head, ""), rest)
                return self._follow(cand, rest)
            if "." not in s:
                break
            s = s.rsplit(".", 1)[0]
        target = ix.imports.get(file, {}).get(head)
        if target:
            return self._follow(target, rest)
        if module and f"{module}.{head}" in ix.symbols:
            return self._follow(f"{module}.{head}", rest)
        return None

    def _follow(self, base: str, rest: List[str]) -> Optional[str]:
        q = ".".join([base] + rest) if rest else base
        sym = self.ix.symbols.get(q)
        if sym is None:
            return None
        if sym.kind == "class" and f"{q}.__init__" in self.ix.symbols:
            return f"{q}.__init__"
        return q if sym.kind in _CALLABLE + ("module",) else None

    def _class_of(self, scope: str) -> Optional[str]:
        parts = scope.split(".")
        for i in range(len(parts), 0, -1):
            sym = self.ix.symbols.get(".".join(parts[:i]))
            if sym is not None and sym.kind == "class":
                return sym.qualified_name
        return None

    def _caller(self, scope: str) -> str:
        parts = scope.split(".")
        for i in range(len(parts), 0, -1):
            q = ".".join(parts[:i])
            sym = self.ix.symbols.get(q)
            if sym is not None and sym.kind in ("function", "method", "module"):
                return q
        return scope

    def _build(self) -> None:
        for sym in self.ix.symbols.values():
            if sym.kind in ("module", "class", "function", "method"):
                self.g.add_node(sym.qualified_name, kind=sym.kind, file=sym.file, line=sym.line, label=sym.name)
        for ref in self.ix.references:
            if ref.ctx != "call":
                continue
            callee = self._resolve(ref.name, ref.file, ref.scope)
            if callee is None:
                continue
            caller = self._caller(ref.scope)
            if caller in self.g and callee in self.g:
                self.g.add_edge(caller, callee, kind="call")
        for file, imports in self.ix.imports.items():
            src_mod = next((m for m, f in self.ix.modules.items() if f == file), None)
            for target in imports.values():
                dst = target if target in self.ix.modules else target.rsplit(".", 1)[0]
                if src_mod and dst in self.ix.modules and dst != src_mod and not self.g.has_edge(src_mod, dst):
                    self.g.add_edge(src_mod, dst, kind="import")

    # ------------------------------------------------------------------ queries
    def callers(self, q: str) -> List[str]:
        return [u for u, _, d in self.g.in_edges(q, data=True) if d.get("kind") == "call"] if q in self.g else []

    def callees(self, q: str) -> List[str]:
        return [v for _, v, d in self.g.out_edges(q, data=True) if d.get("kind") == "call"] if q in self.g else []

    def call_subgraph(self) -> nx.DiGraph:
        return self.g.edge_subgraph([(u, v) for u, v, d in self.g.edges(data=True) if d.get("kind") == "call"]).copy()

    def path(self, src: str, dst: str) -> Optional[List[str]]:
        sub = self.call_subgraph()
        if src not in sub or dst not in sub:
            return None
        try:
            return nx.shortest_path(sub, src, dst)
        except nx.NetworkXNoPath:
            return None

    def is_recursive(self, q: str) -> Tuple[bool, List[str]]:
        sub = self.call_subgraph()
        if q not in sub:
            return False, []
        if sub.has_edge(q, q):
            return True, [q, q]
        for succ in sub.successors(q):
            try:
                back = nx.shortest_path(sub, succ, q)
                return True, [q] + back
            except nx.NetworkXNoPath:
                continue
        return False, []

    def reverse_reach(self, q: str, max_depth: int = 6) -> Dict[str, int]:
        sub = self.call_subgraph()
        if q not in sub:
            return {}
        dist = nx.single_source_shortest_path_length(sub.reverse(copy=False), q, cutoff=max_depth)
        dist.pop(q, None)
        return dist

    def resolve_name(self, name: str) -> List[str]:
        return [s.qualified_name for s in self.ix.lookup(name.split(".")[-1]) if s.kind in _CALLABLE]

    def model(self) -> CallGraphModel:
        nodes = [CallGraphNode(id=n, label=d.get("label", n.split(".")[-1]), kind=d.get("kind", "function"), file=d.get("file"), line=d.get("line")) for n, d in self.g.nodes(data=True) if d.get("kind") != "class" or self.g.degree(n)]
        keep = {n.id for n in nodes}
        edges = [CallGraphEdge(source=u, target=v, kind=d.get("kind", "call")) for u, v, d in self.g.edges(data=True) if u in keep and v in keep]
        return CallGraphModel(nodes=nodes, edges=edges)
