"""Environment + secrets access. Secrets are read ONLY from environment variables."""
from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env once at import time (no-op if the file is absent).
load_dotenv()

_TRUTHY = {"1", "true", "yes", "on"}


def is_dry_run() -> bool:
    """DRY_RUN defaults to ON (safe) unless explicitly disabled."""
    val = os.getenv("DRY_RUN", "1").strip().lower()
    return val in _TRUTHY


def set_dry_run(enabled: bool) -> None:
    """Allow the CLI --dry-run flag to force dry-run for this process."""
    os.environ["DRY_RUN"] = "1" if enabled else "0"


def get_env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)


def require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable '{key}'. "
            f"Set it in your environment or .env file (see .env.example)."
        )
    return val
