"""Configuration schema.

Everything the user can change lives in one YAML file (``/config/config.yaml``).
These pydantic models validate it; invalid configuration is rejected before it
is ever written to disk or applied to the running engine.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONFIG_VERSION = 1

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


def slugify(text: str, existing: set[str] | None = None) -> str:
    """Turn a display name into a unique, URL-safe id."""
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "item"
    if not base[0].isalnum():
        base = "x" + base
    existing = existing or set()
    candidate, n = base, 2
    while candidate in existing:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _check_id(value: str) -> str:
    if not ID_RE.match(value):
        raise ValueError("ids may only contain a-z, 0-9, '-' and '_' (max 48 characters)")
    return value


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)


class Settings(_Model):
    update_interval: float = Field(2.0, ge=1.0, le=60.0, description="Seconds between control cycles")
    stale_timeout: float = Field(60.0, ge=5.0, le=3600.0, description="Seconds without a valid reading before a sensor is stale")
    failsafe_pwm: int = Field(100, ge=50, le=100, description="Fan speed (%) used in fail-safe")
    exit_pwm: int = Field(100, ge=0, le=100, description="Fan speed (%) set on managed fans when the service stops")
    keepalive_interval: float = Field(10.0, ge=2.0, le=300.0, description="Re-send unchanged speeds this often (s)")
    history_interval: int = Field(10, ge=2, le=600, description="Seconds between history samples")
    history_retention_days: int = Field(7, ge=1, le=365)
    backups_to_keep: int = Field(30, ge=1, le=500)


class Auth(_Model):
    enabled: bool = False
    password_hash: str = ""

    @model_validator(mode="after")
    def _need_password(self) -> "Auth":
        if self.enabled and not self.password_hash:
            raise ValueError("set a password before enabling the login")
        return self


class Controller(_Model):
    id: str
    name: str = "OpenFAN"
    type: str = "openfan_serial"
    enabled: bool = True
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def check_id(cls, v: str) -> str:
        return _check_id(v)


class SensorSource(_Model):
    id: str
    name: str
    type: str
    enabled: bool = True
    poll_interval: float = Field(5.0, ge=0.5, le=3600.0)
    options: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def check_id(cls, v: str) -> str:
        return _check_id(v)


class Curve(_Model):
    id: str
    name: str = Field(min_length=1, max_length=80)
    sensors: list[str] = Field(default_factory=list, description="Sensor references: '<source id>:<sensor id>'")
    combine: Literal["max", "avg", "min"] = "max"
    type: Literal["linear", "step"] = "linear"
    points: list[tuple[float, float]] = Field(min_length=1, max_length=32)
    hysteresis: float = Field(2.0, ge=0.0, le=20.0)
    min_pwm: int = Field(0, ge=0, le=100)

    @field_validator("id")
    @classmethod
    def check_id(cls, v: str) -> str:
        return _check_id(v)

    @field_validator("points")
    @classmethod
    def _sort_points(cls, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
        cleaned: list[tuple[float, float]] = []
        for temp, pwm in pts:
            if not -50 <= temp <= 200:
                raise ValueError(f"temperature {temp} is out of range (-50..200 °C)")
            if not 0 <= pwm <= 100:
                raise ValueError(f"fan speed {pwm} is out of range (0..100 %)")
            cleaned.append((float(temp), float(pwm)))
        cleaned.sort(key=lambda p: p[0])
        temps = [p[0] for p in cleaned]
        if len(set(temps)) != len(temps):
            raise ValueError("two points have the same temperature")
        return cleaned

    @field_validator("sensors")
    @classmethod
    def _check_refs(cls, refs: list[str]) -> list[str]:
        out = []
        for ref in refs:
            if ":" not in ref:
                raise ValueError(f"sensor reference '{ref}' must look like 'source:sensor'")
            if ref not in out:
                out.append(ref)
        return out


class Fan(_Model):
    controller: str
    index: int = Field(ge=0, le=63)
    name: str = ""
    curve: str | None = None

    @property
    def key(self) -> str:
        return f"{self.controller}:{self.index}"


class Notifications(_Model):
    enabled: bool = False
    urls: list[str] = Field(default_factory=list, description="Apprise URLs, e.g. tgram://token/chat_id")
    on_failsafe: bool = True
    on_recovery: bool = True
    on_controller: bool = True


class Config(_Model):
    version: int = CONFIG_VERSION
    settings: Settings = Field(default_factory=Settings)
    auth: Auth = Field(default_factory=Auth)
    controllers: list[Controller] = Field(default_factory=list)
    sources: list[SensorSource] = Field(default_factory=list)
    curves: list[Curve] = Field(default_factory=list)
    fans: list[Fan] = Field(default_factory=list)
    notifications: Notifications = Field(default_factory=Notifications)

    @model_validator(mode="after")
    def _references(self) -> "Config":
        def unique(items, what):
            ids = [i.id for i in items]
            dup = {i for i in ids if ids.count(i) > 1}
            if dup:
                raise ValueError(f"duplicate {what} id(s): {', '.join(sorted(dup))}")

        unique(self.controllers, "controller")
        unique(self.sources, "sensor source")
        unique(self.curves, "curve")
        keys = [f.key for f in self.fans]
        dup = {k for k in keys if keys.count(k) > 1}
        if dup:
            raise ValueError(f"fan listed twice: {', '.join(sorted(dup))}")
        # Note: a fan pointing at a curve that no longer exists is *allowed* here on
        # purpose. The engine treats it as a fault and runs the fan in fail-safe, so
        # a broken reference can never silently slow a fan down.
        return self

    # -- helpers ---------------------------------------------------------------
    def curve(self, curve_id: str | None) -> Curve | None:
        return next((c for c in self.curves if c.id == curve_id), None)

    def fan(self, controller: str, index: int) -> Fan | None:
        return next((f for f in self.fans if f.controller == controller and f.index == index), None)

    def controller(self, controller_id: str) -> Controller | None:
        return next((c for c in self.controllers if c.id == controller_id), None)

    def source(self, source_id: str) -> SensorSource | None:
        return next((s for s in self.sources if s.id == source_id), None)
