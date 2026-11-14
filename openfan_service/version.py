"""Version information: build date + git hash, e.g. ``v2026-11-13 [a1b2c3d]``.

Where it comes from, in order:

1. **Docker image** – the build passes ``OPENFAN_VERSION`` (build date, UTC, ``yyyy-mm-dd``)
   and ``OPENFAN_GIT_HASH`` as build arguments; the Dockerfile stores them in the
   environment variables ``OPENFAN_BUILD_VERSION`` / ``OPENFAN_BUILD_HASH``.
2. **Running from a git checkout** – the date of the last commit and its short hash,
   with ``-dirty`` appended when tracked files have uncommitted changes.
3. Otherwise ``dev`` / ``unknown``.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class VersionInfo:
    version: str      # 2026-11-13 or "dev"
    git_hash: str     # a1b2c3d, a1b2c3d-dirty or "unknown"
    source: str       # "build", "git" or "none"

    @property
    def full(self) -> str:
        return f"v{self.version} [{self.git_hash}]"

    def as_dict(self) -> dict[str, str]:
        return {"version": self.version, "git_hash": self.git_hash, "full": self.full, "source": self.source}


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _from_git() -> VersionInfo | None:
    if not (REPO_ROOT / ".git").exists():
        return None
    short = _git("rev-parse", "--short", "HEAD")
    if not short:
        return None  # git missing, or a repository without commits yet
    date = _git("log", "-1", "--format=%cd", "--date=format:%Y-%m-%d") or "dev"
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    if dirty:
        short += "-dirty"
    return VersionInfo(date, short, "git")


@lru_cache(maxsize=1)
def get_version() -> VersionInfo:
    version = os.environ.get("OPENFAN_BUILD_VERSION", "").strip()
    git_hash = os.environ.get("OPENFAN_BUILD_HASH", "").strip()
    if version and version != "dev":
        return VersionInfo(version, git_hash or "unknown", "build")
    return _from_git() or VersionInfo("dev", git_hash or "unknown", "none")
