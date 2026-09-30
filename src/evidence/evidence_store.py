"""Module 2, Step 2.4 — unified Ground Truth Database.

Aggregates symbol tables, static findings, baseline tests, requirement status and the
call graph, indexed by ``file_path`` and line intervals for O(log n) proximity queries.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from src.ingestion.ast_parser import SymbolIndex
from src.ingestion.spec_indexer import RequirementIndex
from src.models.evidence import RequirementStatus, StaticFinding, TestSuiteResult


@dataclass
class GroundTruthDatabase:
    files: Dict[str, str]
    index: SymbolIndex
    findings: List[StaticFinding]
    tests: TestSuiteResult
    requirements: Dict[str, RequirementStatus]
    req_index: RequirementIndex
    call_graph: "object"
    tool_status: Dict[str, str] = field(default_factory=dict)
    _by_file: Dict[str, Tuple[List[int], List[StaticFinding]]] = field(default_factory=dict, init=False)
    _coverage: Dict[str, Dict[int, Set[str]]] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        for f in self.findings:
            enc = self.index.enclosing(f.file, f.line)
            f.function = enc.qualified_name if enc else None
        grouped: Dict[str, List[StaticFinding]] = {}
        for f in sorted(self.findings, key=lambda x: (x.file, x.line)):
            grouped.setdefault(f.file, []).append(f)
        self._by_file = {k: ([x.line for x in v], v) for k, v in grouped.items()}
        for t in self.tests.tests:
            for file, lines in t.lines.items():
                bucket = self._coverage.setdefault(file, {})
                for ln in lines:
                    bucket.setdefault(ln, set()).add(t.test_id)

    # ------------------------------------------------------------------ findings
    def findings_near(self, file: str, start: int, end: int, window: int) -> List[StaticFinding]:
        entry = self._by_file.get(file)
        if not entry:
            return []
        lines, items = entry
        lo = bisect.bisect_left(lines, start - window)
        hi = bisect.bisect_right(lines, end + window)
        return items[lo:hi]

    def findings_in_function(self, qualified: str) -> List[StaticFinding]:
        return [f for f in self.findings if f.function == qualified or (f.function or "").startswith(qualified + ".")]

    # ------------------------------------------------------------------ coverage
    def tests_covering(self, file: str, lines: List[int]) -> Set[str]:
        bucket = self._coverage.get(file, {})
        out: Set[str] = set()
        for ln in lines:
            out |= bucket.get(ln, set())
        return out

    def covered(self, file: str, start: int, end: int) -> bool:
        bucket = self._coverage.get(file, {})
        return any(ln in bucket for ln in range(start, end + 1))

    def requirement(self, req_id: str) -> Optional[RequirementStatus]:
        return self.requirements.get(req_id.upper())
