"""Module 4, Step 4.1 — ephemeral sandbox execution.

Two interchangeable backends execute ``harness.py`` against a materialised workspace:

* ``local``  — a separate interpreter process in a throw-away directory with a
               scrubbed environment, no inherited PYTHONPATH and a hard timeout.
* ``docker`` — an unprivileged, network-less container (``--read-only`` root,
               memory/PID limits, optional gVisor ``runsc`` runtime) with the
               workspace mounted as its working directory.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from config.settings import settings

HARNESS = Path(__file__).with_name("harness.py")

try:
    import docker  # type: ignore
except Exception:  # pragma: no cover
    docker = None


class SandboxResult(dict):
    """Harness output plus execution metadata (``_backend``, ``_elapsed_ms``, ``_error``)."""


def _scrubbed_env(workdir: Path) -> Dict[str, str]:
    keep = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG", "COMSPEC", "PATHEXT", "WINDIR")}
    keep.update({"PYTHONPATH": str(workdir), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONHASHSEED": "0", "PYTHONIOENCODING": "utf-8", "TRUSTCODE_SANDBOX": "1"})
    return keep


def backend_name() -> str:
    if settings.sandbox_backend == "docker" and docker is not None:
        return f"docker{'+' + settings.docker_runtime if settings.docker_runtime else ''}"
    return "local-process"


def run_harness(workdir: Path, mode: str, payload: Dict[str, Any], timeout: Optional[int] = None) -> SandboxResult:
    import time

    control = workdir / ".trustcode"
    control.mkdir(exist_ok=True)
    shutil.copyfile(HARNESS, control / "harness.py")
    token = uuid.uuid4().hex[:8]
    inp, out = control / f"in-{token}.json", control / f"out-{token}.json"
    inp.write_text(json.dumps(payload), encoding="utf-8")
    timeout = timeout or (settings.test_timeout_s if mode == "tests" else settings.task_timeout_s)
    t0 = time.time()
    result = SandboxResult()
    try:
        if settings.sandbox_backend == "docker" and docker is not None:
            _run_docker(workdir, mode, inp.name, out.name, timeout)
        else:
            subprocess.run(
                [sys.executable, "-B", str(control / "harness.py"), mode, str(inp), str(out)],
                cwd=workdir, env=_scrubbed_env(workdir), capture_output=True, timeout=timeout,
            )
        if out.exists():
            result.update(json.loads(out.read_text(encoding="utf-8")))
        else:
            result["_error"] = "harness produced no output (crashed or killed)"
    except subprocess.TimeoutExpired:
        result["_error"] = f"sandbox timeout after {timeout}s"
    except Exception as exc:  # pragma: no cover - defensive
        result["_error"] = f"{exc.__class__.__name__}: {exc}"
    finally:
        for f in (inp, out):
            f.unlink(missing_ok=True)
    result["_backend"] = backend_name()
    result["_elapsed_ms"] = int((time.time() - t0) * 1000)
    return result


def _run_docker(workdir: Path, mode: str, inp: str, out: str, timeout: int) -> None:  # pragma: no cover - needs daemon
    client = docker.from_env()
    kwargs: Dict[str, Any] = dict(
        image=settings.docker_image,
        command=["python", "-B", ".trustcode/harness.py", mode, f".trustcode/{inp}", f".trustcode/{out}"],
        working_dir="/workspace",
        volumes={str(workdir): {"bind": "/workspace", "mode": "rw"}},
        network_disabled=True,
        mem_limit=settings.docker_mem_limit,
        pids_limit=256,
        user="65534:65534",
        cap_drop=["ALL"],
        security_opt=["no-new-privileges"],
        read_only=True,
        tmpfs={"/tmp": "size=64m"},
        environment={"PYTHONPATH": "/workspace", "PYTHONDONTWRITEBYTECODE": "1"},
        detach=True,
    )
    if settings.docker_runtime:
        kwargs["runtime"] = settings.docker_runtime
    container = client.containers.run(**kwargs)
    try:
        container.wait(timeout=timeout)
    finally:
        container.remove(force=True)


def clone_dir(src: Path, dest: Path) -> Path:
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns(".trustcode", "__pycache__", ".pytest_cache"))
    return dest
