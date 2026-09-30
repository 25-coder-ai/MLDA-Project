import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.models.ingestion import AIEvaluationPayload  # noqa: E402
from src.pipeline import TrustPipeline  # noqa: E402
from src.scenarios import demo_files, scenario_payload  # noqa: E402

_CACHE = {}


@pytest.fixture(scope="session")
def files():
    return demo_files()


@pytest.fixture(scope="session")
def run_scenario():
    """Run a built-in scenario once per session and cache the report."""

    def _run(sid: str):
        if sid not in _CACHE:
            _CACHE[sid] = TrustPipeline().run(AIEvaluationPayload(**scenario_payload(sid)))
        return _CACHE[sid]

    return _run
