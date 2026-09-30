"""Module 1, Step 1.1 — repository & workspace ingestion.

Produces an immutable snapshot ``{relative_path: text}`` from inline files, a local
directory, or a remote Git repository (GitPython, with a ``git`` CLI fallback), and
materialises snapshots into isolated per-session directories.
"""
from __future__ import annotations

import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config.settings import settings
from src.models.ingestion import WorkspaceSpec

try:
    import git  # GitPython
except Exception:  # pragma: no cover
    git = None

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", "dist", "build", ".tox", ".trustcode", ".idea", ".vscode"}
TEXT_EXTS = {".py", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".java", ".go", ".md", ".txt", ".toml", ".cfg", ".ini", ".json", ".yaml", ".yml", ".rst", ".sql", ".html", ".css"}
SPEC_HINTS = ("requirement", "spec", "prd", "srs")


@dataclass
class WorkspaceSnapshot:
    session_id: str
    source: str
    files: Dict[str, str]
    commit: Optional[str] = None
    modified: List[str] = field(default_factory=list)

    def spec_documents(self) -> Dict[str, str]:
        return {p: c for p, c in self.files.items() if p.lower().endswith((".md", ".txt")) and any(h in p.lower() for h in SPEC_HINTS)}


def new_session_dir(session_id: str, name: str) -> Path:
    d = settings.work_root / session_id / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_tree(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if len(out) >= settings.max_files:
            break
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
            continue
        if p.suffix.lower() not in TEXT_EXTS or p.stat().st_size > settings.max_file_bytes:
            continue
        try:
            out[rel.as_posix()] = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        except (UnicodeDecodeError, OSError):
            continue
    return out


def _clone(url: str, ref: Optional[str], dest: Path) -> Optional[str]:
    if git is not None:
        repo = git.Repo.clone_from(url, dest, depth=1 if not ref else None)
        if ref:
            repo.git.checkout(ref)
        modified = repo.head.commit.hexsha
        return modified
    subprocess.run(["git", "clone", "--depth", "1", url, str(dest)], check=True, capture_output=True, timeout=300)
    if ref:
        subprocess.run(["git", "-C", str(dest), "checkout", ref], check=True, capture_output=True)
    head = subprocess.run(["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True)
    return head.stdout.strip() or None


def _head_commit(path: Path) -> Optional[str]:
    if git is None:
        return None
    try:
        return git.Repo(path, search_parent_directories=False).head.commit.hexsha
    except Exception:
        return None


def load_workspace(spec: WorkspaceSpec) -> WorkspaceSnapshot:
    session_id = uuid.uuid4().hex[:12]
    if spec.files:
        return WorkspaceSnapshot(session_id=session_id, source="inline", files=dict(spec.files))
    if spec.repo_path:
        root = Path(spec.repo_path).expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"repo_path does not exist: {root}")
        return WorkspaceSnapshot(session_id=session_id, source=f"path:{root}", files=_read_tree(root), commit=_head_commit(root))
    dest = new_session_dir(session_id, "clone")
    commit = _clone(spec.git_url or "", spec.ref, dest)
    return WorkspaceSnapshot(session_id=session_id, source=f"git:{spec.git_url}", files=_read_tree(dest), commit=commit)


def materialise(files: Dict[str, str], dest: Path) -> Path:
    """Write a snapshot to ``dest`` (created fresh)."""
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    return dest


def cleanup_session(session_id: str) -> None:
    if not settings.keep_workspaces:
        shutil.rmtree(settings.work_root / session_id, ignore_errors=True)
