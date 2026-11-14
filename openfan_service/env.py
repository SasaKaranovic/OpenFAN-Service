"""Process-level settings that come from environment variables.

Only things needed before the config file can be read live here. Everything a
user tunes day-to-day is in ``config.yaml`` and editable from the web UI.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Env:
    config_dir: Path
    host: str
    port: int
    log_level: str
    demo: bool
    password: str | None
    serial_port: str | None
    controller_url: str | None
    plugins_enabled: bool


def load_env() -> Env:
    return Env(
        config_dir=Path(os.environ.get("OPENFAN_CONFIG_DIR", "/config")),
        host=os.environ.get("OPENFAN_HOST", "0.0.0.0"),
        port=int(os.environ.get("OPENFAN_PORT", "5340")),
        log_level=os.environ.get("OPENFAN_LOG_LEVEL", "info").upper(),
        demo=_bool("OPENFAN_DEMO"),
        password=os.environ.get("OPENFAN_PASSWORD") or None,
        serial_port=os.environ.get("OPENFAN_SERIAL_PORT") or None,
        controller_url=os.environ.get("OPENFAN_CONTROLLER_URL") or None,
        plugins_enabled=_bool("OPENFAN_USER_PLUGINS", True),
    )


def setup_logging(level: str = "INFO") -> None:
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    logging.basicConfig(stream=sys.stdout, level=getattr(logging, level, logging.INFO), format=fmt, force=True)
    # Third-party libraries are noisy at INFO; keep the log about fans.
    for noisy in ("uvicorn.access", "apprise", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
