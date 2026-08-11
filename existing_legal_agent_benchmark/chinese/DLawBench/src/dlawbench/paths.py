"""Project filesystem paths used by command-line entry points and modules."""

from __future__ import annotations

from pathlib import Path
from typing import Union


PACKAGE_DIR = Path(__file__).resolve().parent
SRC_DIR = PACKAGE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
CASE_BACKGROUNDS_DIR = DATA_DIR / "case_backgrounds"
RUNS_DIR = PROJECT_ROOT / "runs"
ENV_FILE = PROJECT_ROOT / ".env"


def project_path(path: Union[str, Path]) -> Path:
    """Resolve a user-supplied path relative to the project root."""
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def config_path(filename: str) -> Path:
    """Return a file path under config/."""
    return CONFIG_DIR / filename
