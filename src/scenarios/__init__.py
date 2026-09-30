"""Scenario loader: joins a catalog entry with the demo repository files."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from config.settings import PROJECT_ROOT
from src.ingestion.git_loader import _read_tree
from src.scenarios.catalog import SCENARIOS

DEMO_REPO = PROJECT_ROOT / "examples" / "shopcart"


def demo_files() -> Dict[str, str]:
    files = _read_tree(DEMO_REPO)
    return {k: v for k, v in files.items() if k != "REQUIREMENTS.md"}


def demo_requirements() -> str:
    return (DEMO_REPO / "REQUIREMENTS.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def list_scenarios() -> List[Dict]:
    return [{k: s[k] for k in ("id", "title", "tagline", "modes")} | {"claims": s["review_text"].count("\n") + 1 if s["review_text"] else 0, "patches": len(s["patches"]), "judged": len(s["verifier"])} for s in SCENARIOS]


def get_scenario(scenario_id: str) -> Optional[Dict]:
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return {
                **s,
                "files": demo_files(),
                "requirements": [{"name": "REQUIREMENTS.md", "content": demo_requirements()}],
            }
    return None


def scenario_payload(scenario_id: str) -> Dict:
    s = get_scenario(scenario_id)
    if s is None:
        raise KeyError(scenario_id)
    return {
        "workspace": {"files": s["files"]},
        "requirements": s["requirements"],
        "review_text": s["review_text"],
        "patches": s["patches"],
        "verifier": s["verifier"],
    }
