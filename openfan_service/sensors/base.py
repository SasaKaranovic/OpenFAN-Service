"""The sensor plugin interface.

A sensor *plugin* is a type (``unraid_disks``, ``hwmon``, ``command`` ...). A
sensor *source* is one configured instance of a plugin with its own options.
Each source returns any number of named sensors.

Writing a plugin
----------------
Subclass :class:`SensorPlugin`, give it a unique ``type_name`` and implement
:meth:`read`. ``read`` is called every ``poll_interval`` seconds from a worker
thread, so plain blocking code (file reads, subprocess, HTTP) is fine. Return a
mapping ``{sensor_id: Reading}``. Raise an exception if the whole source
failed; every sensor of that source is then reported as an error.

Drop the file into ``/config/plugins/`` and restart the container; the new
type shows up in the web UI under Sensors.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

OK = "ok"
SLEEPING = "sleeping"   # e.g. a spun-down disk; value may be None or 0
ERROR = "error"


@dataclass
class Reading:
    value: float | None
    status: str = OK
    label: str | None = None
    unit: str = "°C"
    message: str | None = None


@dataclass
class Option:
    """Describes one plugin option so the web UI can render a form for it."""

    name: str
    label: str
    type: str = "text"          # text | number | bool | select | textarea | password
    default: Any = None
    help: str = ""
    choices: list[tuple[str, str]] | None = None
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "label": self.label, "type": self.type, "default": self.default,
            "help": self.help, "required": self.required,
            "choices": [{"value": v, "label": lbl} for v, lbl in (self.choices or [])],
        }


class SensorPlugin:
    type_name: ClassVar[str] = ""
    title: ClassVar[str] = ""
    description: ClassVar[str] = ""
    options: ClassVar[list[Option]] = []
    default_poll_interval: ClassVar[float] = 5.0
    #: Push-style plugins receive data through the API instead of being polled.
    push: ClassVar[bool] = False

    def __init__(self, source_id: str, options: dict[str, Any]) -> None:
        self.source_id = source_id
        self.opts = self.resolve_options(options)

    @classmethod
    def resolve_options(cls, options: dict[str, Any] | None) -> dict[str, Any]:
        options = dict(options or {})
        out = {}
        for opt in cls.options:
            val = options.get(opt.name, opt.default)
            if val is None or val == "":
                val = opt.default
            if opt.type == "number" and val is not None:
                try:
                    val = float(val)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"option '{opt.label}' must be a number") from exc
            if opt.type == "bool":
                val = val if isinstance(val, bool) else str(val).lower() in {"1", "true", "yes", "on"}
            if opt.required and (val is None or val == ""):
                raise ValueError(f"option '{opt.label}' is required")
            if opt.type == "select" and opt.choices and val not in {c[0] for c in opt.choices}:
                raise ValueError(f"option '{opt.label}' must be one of: {', '.join(c[0] for c in opt.choices)}")
            out[opt.name] = val
        return out

    @classmethod
    def describe(cls) -> dict[str, Any]:
        return {
            "type": cls.type_name, "title": cls.title or cls.type_name, "description": cls.description,
            "options": [o.as_dict() for o in cls.options], "push": cls.push,
            "default_poll_interval": cls.default_poll_interval,
        }

    def read(self) -> dict[str, Reading]:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:
        """Release resources (optional)."""


@dataclass
class SensorState:
    """What the service knows about one sensor right now."""

    ref: str
    source_id: str
    sensor_id: str
    value: float | None = None
    status: str = ERROR
    label: str | None = None
    unit: str = "°C"
    message: str | None = "waiting for first reading"
    last_ok: float | None = None      # monotonic time of the last good reading
    last_seen_wall: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def age(self, now: float | None = None) -> float | None:
        if self.last_ok is None:
            return None
        return (now or time.monotonic()) - self.last_ok
