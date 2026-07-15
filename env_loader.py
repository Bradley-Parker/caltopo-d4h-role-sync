"""Load KEY=VALUE lines from a .env file into os.environ (no external deps)."""

from __future__ import annotations

import os
from pathlib import Path


def load_env(path: str | Path | None = None) -> None:
    """Load env from ``path``, or ``.env`` next to this module (not cwd).

    Does not override keys that already have a non-empty value. Empty existing
    values are treated as unset so a blank parent/coordinator env can still be
    filled from this tool's ``.env``.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        existing = os.environ.get(key)
        if existing is None or not str(existing).strip():
            os.environ[key] = value
