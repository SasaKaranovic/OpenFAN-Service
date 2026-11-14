"""Loading and saving ``config.yaml`` safely.

Rules:

* Every write is atomic (temp file in the same folder, fsync, rename), so a
  power cut can never leave a half-written config behind.
* Every save first copies the previous file into ``backups/``.
* A config that fails validation is never written and never applied.
* If the file on disk is broken at start-up, the newest valid backup is used
  and the problem is reported in the UI instead of crashing the service.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import Config, Controller, Curve, Fan, SensorSource

log = logging.getLogger("openfan.config")

CONFIG_NAME = "config.yaml"
BACKUP_DIR = "backups"

HEADER = (
    "# OpenFAN Service configuration.\n"
    "# You can edit this file by hand, but the web UI is the easier way.\n"
    "# The service validates every change; a broken file is never applied.\n"
)


class ConfigError(Exception):
    """Raised when a proposed configuration is invalid."""


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "__root__")
        msg = err.get("msg", "invalid value").removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def parse_config(data: Any) -> Config:
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ConfigError("the configuration must be a YAML mapping")
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc


def parse_config_text(text: str) -> Config:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"not valid YAML: {exc}") from exc
    return parse_config(data)


def dump_config(cfg: Config) -> str:
    data = cfg.model_dump(mode="json")
    # Points read better as [temp, pwm] pairs with plain numbers.
    for curve in data.get("curves", []):
        curve["points"] = [[_num(t), _num(v)] for t, v in curve["points"]]
    return HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=None, width=100)


def _num(x: float) -> float | int:
    return int(x) if float(x).is_integer() else x


def default_config(*, demo: bool = False, serial_port: str | None = None, controller_url: str | None = None) -> Config:
    """The configuration a brand-new install starts with."""
    if demo:
        controller = Controller(id="main", name="Demo controller", type="demo", options={"fan_count": 10})
        source = SensorSource(id="demo", name="Demo sensors", type="demo", poll_interval=2)
        curve = Curve(
            id="hdd-cage",
            name="HDD cage",
            sensors=["demo:disk1", "demo:disk2", "demo:disk3"],
            points=[(25, 25), (35, 40), (45, 70), (50, 100)],
            min_pwm=20,
        )
        fans = [Fan(controller="main", index=i, name=f"Fan {i}", curve="hdd-cage" if i < 3 else None) for i in range(10)]
        return Config(controllers=[controller], sources=[source], curves=[curve], fans=fans)
    if controller_url:
        controller = Controller(id="main", name="OpenFAN", type="openfan_rest", options={"url": controller_url})
    else:
        controller = Controller(id="main", name="OpenFAN", type="openfan_serial", options={"port": serial_port or "auto"})
    return Config(controllers=[controller])


class ConfigStore:
    """Thread-safe owner of the current configuration."""

    def __init__(self, config_dir: Path, *, demo: bool = False, serial_port: str | None = None,
                 controller_url: str | None = None) -> None:
        self.dir = Path(config_dir)
        self.path = self.dir / CONFIG_NAME
        self.backup_dir = self.dir / BACKUP_DIR
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Config, Config], None]] = []
        self.load_warning: str | None = None
        self._defaults = dict(demo=demo, serial_port=serial_port, controller_url=controller_url)
        self._config = self._load()

    # -- reading -----------------------------------------------------------------
    @property
    def config(self) -> Config:
        with self._lock:
            return self._config

    def _load(self) -> Config:
        self.dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            cfg = default_config(**self._defaults)
            log.info("No %s found in %s, creating a new one", CONFIG_NAME, self.dir)
            self._write(cfg, backup=False)
            return cfg
        try:
            cfg = parse_config_text(self.path.read_text(encoding="utf-8"))
            log.info("Loaded configuration from %s", self.path)
            return cfg
        except (ConfigError, OSError, UnicodeDecodeError) as exc:
            log.error("Configuration file %s is invalid: %s", self.path, exc)
            for backup in self.list_backups():
                try:
                    cfg = parse_config_text((self.backup_dir / backup["name"]).read_text(encoding="utf-8"))
                except (ConfigError, OSError, UnicodeDecodeError):
                    continue
                self.load_warning = (
                    f"{CONFIG_NAME} could not be read ({exc}). Running from backup {backup['name']}. "
                    "Saving any change in the UI will replace the broken file."
                )
                log.warning(self.load_warning)
                return cfg
            self.load_warning = (
                f"{CONFIG_NAME} could not be read ({exc}) and no valid backup exists. "
                "Running with an empty configuration; the broken file was left untouched."
            )
            log.warning(self.load_warning)
            return default_config(**self._defaults)

    # -- writing -----------------------------------------------------------------
    def on_change(self, callback: Callable[[Config, Config], None]) -> None:
        self._listeners.append(callback)

    def replace(self, new: Config) -> Config:
        """Validate (already done by the model), persist and publish a new config."""
        # Round-trip through the dumper to be sure what we save is what we load.
        new = parse_config_text(dump_config(new))
        with self._lock:
            old = self._config
            self._write(new, backup=True)
            self._config = new
            self.load_warning = None
        for cb in list(self._listeners):
            try:
                cb(old, new)
            except Exception:  # a listener must never break saving
                log.exception("Config listener failed")
        return new

    def mutate(self, fn: Callable[[dict[str, Any]], Any]) -> Config:
        """Apply ``fn`` to a plain-dict copy of the config, validate and save it."""
        with self._lock:
            data = self._config.model_dump(mode="python")
            fn(data)
            new = parse_config(data)
            return self.replace(new)

    def _write(self, cfg: Config, *, backup: bool) -> None:
        text = dump_config(cfg)
        self.dir.mkdir(parents=True, exist_ok=True)
        if backup and self.path.exists():
            self._backup_current()
        fd, tmp = tempfile.mkstemp(prefix=".config-", suffix=".tmp", dir=self.dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    pass  # some network shares do not support fsync
            try:
                os.replace(tmp, self.path)
            except OSError:
                # Rare: file systems that refuse rename-over. Fall back to copy.
                shutil.copyfile(tmp, self.path)
                os.unlink(tmp)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -- backups -----------------------------------------------------------------
    def _backup_current(self) -> None:
        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")[:-3]
            shutil.copyfile(self.path, self.backup_dir / f"config-{stamp}.yaml")
            self._prune_backups()
        except OSError as exc:
            log.warning("Could not write backup: %s", exc)

    def _prune_backups(self) -> None:
        keep = self._config.settings.backups_to_keep if hasattr(self, "_config") else 30
        for item in self.list_backups()[keep:]:
            try:
                (self.backup_dir / item["name"]).unlink()
            except OSError:
                pass

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_dir.is_dir():
            return []
        items = []
        for p in self.backup_dir.glob("config-*.yaml"):
            try:
                st = p.stat()
            except OSError:
                continue
            items.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime})
        items.sort(key=lambda i: i["name"], reverse=True)
        return items

    def read_backup(self, name: str) -> str:
        if "/" in name or "\\" in name or not name.startswith("config-") or not name.endswith(".yaml"):
            raise ConfigError("invalid backup name")
        path = self.backup_dir / name
        if not path.is_file():
            raise ConfigError("backup not found")
        return path.read_text(encoding="utf-8")

    def export_text(self) -> str:
        return dump_config(self.config)
