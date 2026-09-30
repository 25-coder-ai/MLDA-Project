"""Background verification jobs with live stage progress.

Default: in-process thread pool (zero infrastructure). When ``TRUSTCODE_REDIS_URL`` is
set and Celery is installed, jobs are dispatched to the Celery worker defined in
``src/worker.py`` instead (see docker-compose.yml).
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from config.settings import settings
from src.models.ingestion import AIEvaluationPayload
from src.pipeline import STAGES, TrustPipeline

_MAX_JOBS = 50


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="trustcode-job")

    def _new(self) -> Dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "job_id": job_id,
            "status": "queued",
            "created_at": time.time(),
            "progress": 0,
            "message": "queued",
            "stages": [{"key": k, "label": label, "status": "pending", "message": ""} for k, label in STAGES],
            "log": [],
            "report": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
            if len(self._jobs) > _MAX_JOBS:
                oldest = sorted(self._jobs.values(), key=lambda j: j["created_at"])[0]["job_id"]
                self._jobs.pop(oldest, None)
        return job

    def _event(self, job: Dict[str, Any], e: Dict[str, Any]) -> None:
        with self._lock:
            job["progress"] = max(job["progress"], int(e.get("progress", 0)))
            job["message"] = str(e.get("message", ""))
            job["log"].append({"t": round(float(e.get("t", time.time())) - job["created_at"], 2), "stage": e["stage"], "message": e.get("message", "")})
            for s in job["stages"]:
                if s["key"] == e["stage"]:
                    s["status"] = "done" if e["status"] == "done" else "running"
                    s["message"] = e.get("message", "")

    def submit(self, payload: AIEvaluationPayload) -> str:
        job = self._new()

        def run() -> None:
            job["status"] = "running"
            try:
                report = TrustPipeline(on_event=lambda e: self._event(job, e)).run(payload)
                with self._lock:
                    job["report"] = report.model_dump(mode="json")
                    job["status"] = "done"
                    job["progress"] = 100
                    for s in job["stages"]:
                        s["status"] = "done"
            except Exception as exc:  # pragma: no cover - surfaced to the UI
                with self._lock:
                    job["status"] = "error"
                    job["error"] = f"{exc.__class__.__name__}: {exc}"
                    job["log"].append({"t": round(time.time() - job["created_at"], 2), "stage": "error", "message": traceback.format_exc()[-1200:]})

        if settings.redis_url:
            try:
                from src.worker import verify_task  # pragma: no cover - requires broker

                verify_task.apply_async(args=[payload.model_dump(mode="json")], task_id=job["job_id"])
                job["celery"] = True
                job["status"] = "running"
                job["message"] = "dispatched to Celery"
                return job["job_id"]
            except Exception:
                pass
        self._pool.submit(run)
        return job["job_id"]

    def _sync_celery(self, job: Dict[str, Any]) -> None:  # pragma: no cover - requires broker
        from src.worker import app

        res = app.AsyncResult(job["job_id"])
        if res.state == "PROGRESS" and isinstance(res.info, dict):
            self._event(job, {**res.info, "t": time.time()})
        elif res.state == "SUCCESS":
            job.update(report=res.result, status="done", progress=100)
            for s in job["stages"]:
                s["status"] = "done"
        elif res.state == "FAILURE":
            job.update(status="error", error=str(res.result))

    def get(self, job_id: str, include_report: bool = True) -> Optional[Dict[str, Any]]:
        job = self._jobs.get(job_id)
        if job is not None and job.get("celery") and job["status"] == "running":
            self._sync_celery(job)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            out = {k: v for k, v in job.items() if k != "report" or include_report}
            out["log"] = list(job["log"][-40:])
            out["stages"] = [dict(s) for s in job["stages"]]
            return out


jobs = JobStore()
