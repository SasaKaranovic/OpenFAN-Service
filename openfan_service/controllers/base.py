"""Fan controller driver interface.

Drivers are plain blocking classes. The :class:`~.manager.ControllerManager`
runs every call of one controller in its own single worker thread, so a
driver never has to worry about concurrent access to its port or socket, and a
hung call can never block the control loop of other controllers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from ..sensors.base import Option


class ControllerError(Exception):
    pass


@dataclass
class ControllerInfo:
    name: str
    fan_count: int
    hardware: str = ""
    firmware: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ControllerDriver:
    type_name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    options: ClassVar[list[Option]] = []
    #: True if the controller can report the fan names (aliases) stored on it.
    supports_fan_names: ClassVar[bool] = False

    def __init__(self, controller_id: str, options: dict[str, Any]) -> None:
        self.controller_id = controller_id
        self.opts = _resolve(self.options, options)

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {"type": cls.type_name, "title": cls.title, "description": cls.description,
                "options": [o.as_dict() for o in cls.options], "supports_fan_names": cls.supports_fan_names}

    @property
    def target(self) -> str:
        """Human-readable connection target (serial port or URL)."""
        return ""

    def connect(self) -> ControllerInfo:  # pragma: no cover - interface
        raise NotImplementedError

    def set_pwm(self, index: int, pwm: int) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def read_rpm(self) -> dict[int, int]:  # pragma: no cover - interface
        raise NotImplementedError

    def read_fan_names(self) -> dict[int, str]:
        """Fan names stored on the controller. Only drivers with ``supports_fan_names``."""
        raise ControllerError("this controller type cannot report fan names")

    def close(self) -> None:
        pass


def _resolve(spec: list[Option], options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(options or {})
    out = {}
    for opt in spec:
        val = options.get(opt.name, opt.default)
        if val is None or val == "":
            val = opt.default
        if opt.type == "number" and val is not None:
            try:
                val = float(val)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"option '{opt.label}' must be a number") from exc
        if opt.required and (val is None or val == ""):
            raise ValueError(f"option '{opt.label}' is required")
        out[opt.name] = val
    return out
