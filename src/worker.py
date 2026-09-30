"""Optional Celery worker (SOP §3: Celery + Redis task dispatching).

    celery -A src.worker worker --loglevel=info

Only imported when ``TRUSTCODE_REDIS_URL`` is configured.
"""
from __future__ import annotations

from celery import Celery  # type: ignore

from config.settings import settings
from src.models.ingestion import AIEvaluationPayload
from src.pipeline import TrustPipeline

app = Celery("trustcode", broker=settings.redis_url, backend=settings.redis_url)
app.conf.task_track_started = True


@app.task(bind=True, name="trustcode.verify")
def verify_task(self, payload: dict) -> dict:
    def progress(e: dict) -> None:
        self.update_state(state="PROGRESS", meta={k: e[k] for k in ("stage", "status", "message", "progress")})

    report = TrustPipeline(on_event=progress).run(AIEvaluationPayload(**payload))
    return report.model_dump(mode="json")
