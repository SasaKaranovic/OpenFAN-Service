"""Finds built-in plugins and user plugins dropped into ``/config/plugins``."""

from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from .base import SensorPlugin
from .plugins import BUILTIN

log = logging.getLogger("openfan.plugins")


class PluginRegistry:
    def __init__(self) -> None:
        self.types: dict[str, type[SensorPlugin]] = {p.type_name: p for p in BUILTIN}
        self.load_errors: list[str] = []

    def load_user_plugins(self, folder: Path) -> None:
        folder = Path(folder)
        if not folder.is_dir():
            return
        for path in sorted(folder.glob("*.py")):
            if path.name.startswith("_"):
                continue
            try:
                spec = importlib.util.spec_from_file_location(f"openfan_user_plugin_{path.stem}", path)
                module = importlib.util.module_from_spec(spec)
                assert spec.loader is not None
                spec.loader.exec_module(module)
            except Exception as exc:  # a broken user plugin must not stop the service
                msg = f"{path.name}: {exc}"
                self.load_errors.append(msg)
                log.error("Could not load plugin %s", msg)
                continue
            found = 0
            for _, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, SensorPlugin) and obj is not SensorPlugin and obj.type_name:
                    if obj.type_name in self.types and self.types[obj.type_name].__module__ != module.__name__:
                        msg = f"{path.name}: type '{obj.type_name}' already exists"
                        self.load_errors.append(msg)
                        log.error(msg)
                        continue
                    self.types[obj.type_name] = obj
                    found += 1
            log.info("Loaded %d plugin type(s) from %s", found, path.name)

    def get(self, type_name: str) -> type[SensorPlugin] | None:
        return self.types.get(type_name)

    def describe(self) -> list[dict]:
        return [cls.describe() for cls in self.types.values()]
