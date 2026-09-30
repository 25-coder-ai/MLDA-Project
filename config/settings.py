"""Runtime configuration for TRUST-CODE (Pydantic BaseSettings).

Every value can be overridden with an environment variable prefixed with
``TRUSTCODE_`` (e.g. ``TRUSTCODE_SANDBOX_BACKEND=docker``) or via a ``.env`` file.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ScoringWeights(BaseSettings):
    """Step 5.1 default weights. Penalties are stored as magnitudes and subtracted."""

    model_config = SettingsConfigDict(env_prefix="TRUSTCODE_W_", extra="ignore")

    base: float = 50.0
    supported: float = 25.0
    unsupported: float = 15.0
    contradicted: float = 35.0
    hallucinated: float = 50.0


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRUSTCODE_", env_file=".env", extra="ignore")

    app_name: str = "TRUST-CODE"
    version: str = "1.0.0"

    # Workspaces --------------------------------------------------------------
    work_root: Path = Field(default_factory=lambda: Path(tempfile.gettempdir()) / "trustcode")
    keep_workspaces: bool = False
    max_file_bytes: int = 400_000
    max_files: int = 600

    # Sandbox (Module 4.1) ------------------------------------------------------
    sandbox_backend: Literal["local", "docker"] = "local"
    docker_image: str = "python:3.12-slim"
    docker_runtime: Optional[str] = None  # "runsc" enables gVisor kernel isolation
    docker_mem_limit: str = "512m"
    test_timeout_s: int = 120
    task_timeout_s: int = 60
    probe_call_timeout_s: float = 1.5
    parallelism: int = 4

    # Evidence gathering (Module 2) ---------------------------------------------
    use_bandit: bool = True
    use_semgrep: bool = True
    semgrep_config: str = "auto"
    tool_timeout_s: int = 180
    evidence_line_window: int = 5

    # Patch validation extensions ------------------------------------------------
    max_mutants: int = 10
    max_probe_cases: int = 80
    proof_exhaustive_limit: int = 20_000
    proof_sample_size: int = 3_000

    # Requirement retrieval ------------------------------------------------------
    spec_chunk_tokens: int = 500
    spec_chunk_overlap: int = 50
    requirement_link_threshold: float = 0.22
    use_langchain_splitter: bool = False  # opt-in; the tiktoken chunker is equivalent and lighter

    # Verdict boundaries (Step 5.3) ---------------------------------------------
    trusted_threshold: float = 80.0
    uncertain_threshold: float = 50.0

    # Optional distributed execution ---------------------------------------------
    redis_url: Optional[str] = None

    weights: ScoringWeights = Field(default_factory=ScoringWeights)


settings = Settings()
