"""Shared helpers for turning text into sensor readings."""

from __future__ import annotations

import json
import re
from typing import Any

from .base import Reading

NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_id(text: str) -> str:
    return _SAFE_ID.sub("_", str(text).strip()).strip("_") or "sensor"


def to_float(raw: Any) -> float | None:
    """Parse a number from loose input ('45', '45.5', '45,5', ' 45 °C'). None if impossible."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        val = float(raw)
    else:
        match = NUMBER_RE.search(str(raw))
        if not match:
            return None
        val = float(match.group(0).replace(",", "."))
    if val != val or val in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return val


def reading_from(raw: Any, scale: float = 1.0, label: str | None = None) -> Reading:
    val = to_float(raw)
    if val is None:
        return Reading(None, "error", label=label, message=f"not a number: {str(raw)[:40]!r}")
    return Reading(round(val * scale, 2), label=label)


def _walk(data: Any, path: str) -> Any:
    cur = data
    for part in [p for p in path.split(".") if p]:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise KeyError(path)
    return cur


def parse_fields(spec: str) -> list[tuple[str, str]]:
    """Parse 'id = path' lines (blank lines and # comments ignored)."""
    out = []
    for line in (spec or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line:
            key, path = line.split("=", 1)
            out.append((safe_id(key.strip()), path.strip()))
        else:
            out.append((safe_id(line.split(".")[-1]), line))
    return out


def parse_text(text: str, fmt: str, *, sensor_id: str = "value", scale: float = 1.0,
               fields: str = "") -> dict[str, Reading]:
    """Parse plugin output in one of the supported formats.

    * ``number`` – the whole text is one number (``sensor_id`` names it)
    * ``lines``  – one sensor per line: ``name=value``, ``name: value`` or ``name value``
    * ``json``   – a JSON object. With ``fields`` ("id = path.to.value" per line) only
      those values are used; otherwise every top-level number becomes a sensor.
    """
    if fmt == "number":
        return {safe_id(sensor_id): reading_from(text.strip(), scale)}

    if fmt == "lines":
        out: dict[str, Reading] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^\s*([^=:\s]+)\s*[=:\s]\s*(.+)$", line)
            if m:
                out[safe_id(m.group(1))] = reading_from(m.group(2), scale)
        return out

    if fmt == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON: {exc.msg}") from exc
        return parse_json(data, scale=scale, fields=fields)

    raise ValueError(f"unknown format '{fmt}'")


def parse_json(data: Any, *, scale: float = 1.0, fields: str = "") -> dict[str, Reading]:
    out: dict[str, Reading] = {}
    wanted = parse_fields(fields)
    if wanted:
        for sid, path in wanted:
            try:
                out[sid] = reading_from(_walk(data, path), scale)
            except (KeyError, IndexError, ValueError, TypeError):
                out[sid] = Reading(None, "error", message=f"'{path}' not found in response")
        return out
    if isinstance(data, (int, float)) and not isinstance(data, bool):
        return {"value": reading_from(data, scale)}
    if not isinstance(data, dict):
        raise ValueError("JSON must be an object (or set 'Fields' to pick values)")
    for key, val in data.items():
        if isinstance(val, dict) and "value" in val:
            out[safe_id(key)] = reading_from(val["value"], scale, label=val.get("label"))
        elif to_float(val) is not None and not isinstance(val, (dict, list)):
            out[safe_id(key)] = reading_from(val, scale)
    return out
