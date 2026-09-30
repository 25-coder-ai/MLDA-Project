"""Unified-diff parsing, validation and atomic application (Step 4.2).

LLM-generated diffs are frequently malformed (wrong hunk counts, invented context).
The parser recomputes hunk counts instead of trusting headers, and the applier is
strict about context: a hunk whose context lines do not exist in the file is
rejected — that is a *hallucinated patch context*, not a merge conflict.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.models.scoring import HunkReport, PatchApplication

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int
    header: str
    lines: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def old_block(self) -> List[str]:
        return [t for tag, t in self.lines if tag in (" ", "-")]

    @property
    def new_block(self) -> List[str]:
        return [t for tag, t in self.lines if tag in (" ", "+")]


@dataclass
class FilePatch:
    old_path: Optional[str]
    new_path: Optional[str]
    hunks: List[Hunk] = field(default_factory=list)

    @property
    def path(self) -> str:
        return (self.new_path or self.old_path or "").strip()


class DiffError(ValueError):
    pass


def _strip(p: str) -> Optional[str]:
    p = p.split("\t")[0].strip()
    if p == "/dev/null":
        return None
    return p[2:] if p[:2] in ("a/", "b/") else p


def parse_unified_diff(text: str, default_file: Optional[str] = None) -> Tuple[List[FilePatch], List[str]]:
    warnings: List[str] = []
    patches: List[FilePatch] = []
    cur: Optional[FilePatch] = None
    hunk: Optional[Hunk] = None
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            cur = FilePatch(_strip(line[4:]), _strip(lines[i + 1][4:]))
            patches.append(cur)
            hunk = None
            i += 2
            continue
        m = HUNK.match(line)
        if m:
            if cur is None:
                if not default_file:
                    raise DiffError("hunk found before any ---/+++ file header")
                cur = FilePatch(default_file, default_file)
                patches.append(cur)
                warnings.append("diff has no file headers; assumed target_file")
            hunk = Hunk(int(m.group(1)), int(m.group(2) or 1), int(m.group(3)), int(m.group(4) or 1), line)
            cur.hunks.append(hunk)
            i += 1
            continue
        if hunk is not None and line[:1] in (" ", "+", "-") and not line.startswith(("--- ", "+++ ")):
            hunk.lines.append((line[0], line[1:]))
        elif hunk is not None and line == "" and i < len(lines) - 1:
            hunk.lines.append((" ", ""))  # blank context line whose leading space was stripped
        elif line.startswith("\\ No newline"):
            pass
        i += 1
    if not patches or not any(p.hunks for p in patches):
        raise DiffError("no hunks found — not a unified diff (POSIX patch format)")
    for p in patches:
        for h in p.hunks:
            while h.lines and h.lines[-1] == (" ", ""):
                h.lines.pop()
            old_n, new_n = len(h.old_block), len(h.new_block)
            if (old_n, new_n) != (h.old_len, h.new_len):
                warnings.append(f"{p.path}: hunk header {h.header.split('@@')[1].strip()} miscounts lines (actual -{old_n} +{new_n}); recomputed")
                h.old_len, h.new_len = old_n, new_n
    return patches, warnings


def _find_block(src: List[str], block: List[str], hint: int) -> Optional[int]:
    norm = [b.rstrip() for b in block]
    if not norm:
        return max(0, min(hint, len(src)))
    n = len(norm)
    candidates = sorted(range(0, len(src) - n + 1), key=lambda k: abs(k - hint))
    for k in candidates:
        if all(src[k + j].rstrip() == norm[j] for j in range(n)):
            return k
    return None


def apply_patches(files: Dict[str, str], patches: List[FilePatch]) -> Tuple[Dict[str, str], PatchApplication]:
    app = PatchApplication(applied=True)
    out = dict(files)
    for fp in patches:
        path = fp.path
        if fp.old_path is None:
            src: List[str] = []
        elif fp.old_path not in out:
            app.applied = False
            app.error = f"patch targets `{fp.old_path}`, which does not exist in the workspace"
            for h in fp.hunks:
                app.hunks.append(HunkReport(file=fp.old_path, header=h.header, status="rejected", note="file not found"))
            continue
        else:
            src = out[fp.old_path].split("\n")
            if src and src[-1] == "":
                src = src[:-1]
        shift = 0
        changed: List[int] = []
        removed: List[int] = []
        for h in fp.hunks:
            hint = h.old_start - 1 + shift
            pos = _find_block(src, h.old_block, hint)
            if pos is None:
                app.applied = False
                missing = next((b for b in h.old_block if b.strip() and b.rstrip() not in (s.rstrip() for s in src)), None)
                note = f"context line not present in file: {missing.strip()!r}" if missing else "context lines exist but not contiguously (stale or invented context)"
                app.hunks.append(HunkReport(file=path, header=h.header, status="rejected", note=note))
                app.error = app.error or f"hunk {h.header} does not apply to `{path}` — {note}"
                continue
            offset = pos - hint
            new_line = pos + 1
            old_line = pos + 1
            for tag, _ in h.lines:
                if tag == "+":
                    changed.append(new_line)
                    new_line += 1
                elif tag == "-":
                    removed.append(old_line)
                    old_line += 1
                else:
                    new_line += 1
                    old_line += 1
            src[pos : pos + len(h.old_block)] = h.new_block
            shift += len(h.new_block) - len(h.old_block)
            app.hunks.append(HunkReport(file=path, header=h.header, status="applied" if offset == 0 else "offset", offset=offset, note="" if offset == 0 else f"applied with offset {offset:+d} lines"))
        if fp.new_path is None:
            out.pop(fp.old_path or "", None)
        else:
            out[fp.new_path] = "\n".join(src) + "\n"
            if fp.old_path and fp.old_path != fp.new_path:
                out.pop(fp.old_path, None)
        app.files_touched.append(path)
        app.changed_lines[path] = sorted(set(changed))
        app.removed_lines[path] = sorted(set(removed))
    return out, app


def git_apply_check(files: Dict[str, str], diff: str, touched: List[str]) -> Optional[str]:
    """Cross-check with ``git apply --check`` (SOP 4.2); ``None`` when git is unavailable."""
    if shutil.which("git") is None:
        return None
    with tempfile.TemporaryDirectory(prefix="tc-gitcheck-") as d:
        for rel in touched:
            if rel in files:
                p = Path(d) / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(files[rel], encoding="utf-8", newline="\n")
        (Path(d) / "patch.diff").write_text(diff, encoding="utf-8", newline="\n")
        try:
            subprocess.run(["git", "init", "-q"], cwd=d, capture_output=True, timeout=30)
            proc = subprocess.run(["git", "-c", "core.autocrlf=false", "apply", "--check", "--recount", "patch.diff"], cwd=d, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return "ok" if proc.returncode == 0 else (proc.stderr.strip().splitlines() or ["rejected"])[0][:200]
