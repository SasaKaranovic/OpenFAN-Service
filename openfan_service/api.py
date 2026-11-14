"""HTTP API used by the web UI (and by anyone who wants to script the service)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from .version import get_version
from .auth import COOKIE, SESSION_DAYS, SessionSigner, hash_password, verify_password
from .config_store import ConfigError, ConfigStore, format_validation_error, parse_config_text
from .controllers import DRIVERS
from .controllers.drivers import OpenFanSerial
from .engine import Engine
from .events import EventLog
from .history import History
from .models import Curve, slugify
from .notifier import Notifier
from .sensors import PluginRegistry
from .sensors.manager import SensorManager
from .sensors.plugins import PushSensor

from pydantic import ValidationError


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Services:
    store: ConfigStore
    registry: PluginRegistry
    sensors: SensorManager
    engine: Engine
    history: History
    notifier: Notifier
    events: EventLog
    signer: SessionSigner
    env_password: bool = False


def ok(data: Any = None, **extra: Any) -> JSONResponse:
    body = {"ok": True, "data": data}
    body.update(extra)
    return JSONResponse(body)


async def body_json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApiError("request body must be JSON") from exc
    if not isinstance(data, dict):
        raise ApiError("request body must be a JSON object")
    return data


def svc(request: Request) -> Services:
    return request.app.state.services


def save(request: Request, fn) -> Any:
    try:
        return svc(request).store.mutate(fn)
    except ConfigError as exc:
        raise ApiError(str(exc)) from exc
    except ValidationError as exc:
        raise ApiError(format_validation_error(exc)) from exc


def public_config(cfg) -> dict[str, Any]:
    data = cfg.model_dump(mode="json")
    data["auth"] = {"enabled": cfg.auth.enabled, "has_password": bool(cfg.auth.password_hash)}
    for src in data["sources"]:
        if src.get("options", {}).get("token"):
            src["options"]["token"] = "********"
    return data


# ---------------------------------------------------------------------------
async def health(request: Request) -> Response:
    s = svc(request)
    snap = s.engine.snapshot()["engine"]
    status = 200 if snap["running"] or s.engine.cycles == 0 else 503
    return JSONResponse({"ok": status == 200, "engine": snap, "version": get_version().full,
                         "version_info": get_version().as_dict()}, status_code=status)


async def state(request: Request) -> Response:
    s = svc(request)
    snap = s.engine.snapshot()
    warnings = []
    if s.store.load_warning:
        warnings.append(s.store.load_warning)
    if s.history.error:
        warnings.append(s.history.error)
    warnings += [f"Plugin failed to load: {e}" for e in s.registry.load_errors]
    if s.notifier.last_error:
        warnings.append(f"Last notification failed: {s.notifier.last_error}")
    return ok({
        **snap,
        "sensors": s.sensors.snapshot(),
        "sources": s.sensors.sources_snapshot(),
        "controllers": s.engine.controllers.snapshot(),
        "events": s.events.recent(30),
        "notifications": s.notifier.status(),
        "warnings": warnings,
        "version": get_version().full, "version_info": get_version().as_dict(),
        "time": time.time(),
    })


async def get_config(request: Request) -> Response:
    return ok(public_config(svc(request).store.config))


async def meta(request: Request) -> Response:
    s = svc(request)
    return ok({
        "sensor_types": s.registry.describe(),
        "controller_types": [d.describe() for d in DRIVERS.values()],
        "version": get_version().full, "version_info": get_version().as_dict(),
    })


async def serial_ports(request: Request) -> Response:
    return ok(await asyncio.to_thread(OpenFanSerial.list_ports))


# -- curves -----------------------------------------------------------------------
def _curve_from(body: dict[str, Any], curve_id: str) -> dict[str, Any]:
    allowed = {"name", "sensors", "combine", "type", "points", "hysteresis", "min_pwm"}
    data = {k: v for k, v in body.items() if k in allowed}
    data["id"] = curve_id
    try:
        Curve.model_validate(data)
    except ValidationError as exc:
        raise ApiError(format_validation_error(exc)) from exc
    return data


def _assign(data: dict[str, Any], curve_id: str, fan_keys: list[str] | None) -> None:
    if fan_keys is None:
        return
    wanted = set(fan_keys)
    existing = {f"{f['controller']}:{f['index']}": f for f in data["fans"]}
    for key, fan in existing.items():
        if fan.get("curve") == curve_id and key not in wanted:
            fan["curve"] = None
    for key in wanted:
        if ":" not in key:
            raise ApiError(f"bad fan key '{key}'")
        cid, idx = key.rsplit(":", 1)
        if key in existing:
            existing[key]["curve"] = curve_id
        else:
            data["fans"].append({"controller": cid, "index": int(idx), "name": "", "curve": curve_id})


async def create_curve(request: Request) -> Response:
    body = await body_json(request)
    existing = {c.id for c in svc(request).store.config.curves}
    curve_id = slugify(str(body.get("name") or "curve"), existing)
    curve = _curve_from(body, curve_id)

    def fn(data):
        data["curves"].append(curve)
        _assign(data, curve_id, body.get("fans"))
    save(request, fn)
    return ok({"id": curve_id})


async def update_curve(request: Request) -> Response:
    curve_id = request.path_params["curve_id"]
    body = await body_json(request)
    curve = _curve_from(body, curve_id)

    def fn(data):
        for i, c in enumerate(data["curves"]):
            if c["id"] == curve_id:
                data["curves"][i] = curve
                break
        else:
            raise ApiError("curve not found", 404)
        _assign(data, curve_id, body.get("fans"))
    save(request, fn)
    return ok({"id": curve_id})


async def delete_curve(request: Request) -> Response:
    curve_id = request.path_params["curve_id"]

    def fn(data):
        before = len(data["curves"])
        data["curves"] = [c for c in data["curves"] if c["id"] != curve_id]
        if len(data["curves"]) == before:
            raise ApiError("curve not found", 404)
        # Fans keep pointing at the deleted curve on purpose: the engine runs them in
        # fail-safe until the user picks a new curve for them.
    save(request, fn)
    return ok()


# -- fans -------------------------------------------------------------------------
async def update_fan(request: Request) -> Response:
    cid = request.path_params["controller"]
    idx = int(request.path_params["index"])
    body = await body_json(request)

    def fn(data):
        fan = next((f for f in data["fans"] if f["controller"] == cid and f["index"] == idx), None)
        if fan is None:
            fan = {"controller": cid, "index": idx, "name": "", "curve": None}
            data["fans"].append(fan)
        if "name" in body:
            fan["name"] = str(body["name"] or "")[:60]
        if "curve" in body:
            fan["curve"] = body["curve"] or None
    save(request, fn)
    return ok()


# -- sensor sources -------------------------------------------------------------
def _source_from(request: Request, body: dict[str, Any], source_id: str, old: dict | None = None) -> dict[str, Any]:
    s = svc(request)
    type_name = body.get("type") or (old or {}).get("type")
    cls = s.registry.get(type_name or "")
    if cls is None:
        raise ApiError(f"unknown sensor type '{type_name}'")
    options = dict(body.get("options") or {})
    if old and options.get("token") == "********":
        options["token"] = old.get("options", {}).get("token", "")
    try:
        cls.resolve_options(options)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    ignored = body.get("ignored", (old or {}).get("ignored", []))
    return {
        "id": source_id, "type": type_name, "ignored": [str(x) for x in ignored or []],
        "name": str(body.get("name") or cls.title or type_name)[:60],
        "enabled": bool(body.get("enabled", True)),
        "poll_interval": float(body.get("poll_interval") or cls.default_poll_interval),
        "options": options,
    }


async def create_source(request: Request) -> Response:
    body = await body_json(request)
    existing = {c.id for c in svc(request).store.config.sources}
    wanted = str(body.get("id") or "").strip().lower()
    source_id = wanted if wanted and wanted not in existing else slugify(str(body.get("name") or body.get("type") or "source"), existing)
    src = _source_from(request, body, source_id)
    save(request, lambda data: data["sources"].append(src))
    return ok({"id": source_id})


async def update_source(request: Request) -> Response:
    sid = request.path_params["source_id"]
    body = await body_json(request)

    def fn(data):
        for i, c in enumerate(data["sources"]):
            if c["id"] == sid:
                data["sources"][i] = _source_from(request, body, sid, c)
                return
        raise ApiError("sensor source not found", 404)
    save(request, fn)
    return ok()


async def delete_source(request: Request) -> Response:
    sid = request.path_params["source_id"]

    def fn(data):
        before = len(data["sources"])
        data["sources"] = [c for c in data["sources"] if c["id"] != sid]
        if len(data["sources"]) == before:
            raise ApiError("sensor source not found", 404)
    save(request, fn)
    return ok()


async def ignore_sensor(request: Request) -> Response:
    """Ignore or restore one sensor: {"ref": "unraid:parity2", "ignored": true}."""
    body = await body_json(request)
    ref = str(body.get("ref") or "")
    source_id, _, sensor_id = ref.partition(":")
    if not source_id or not sensor_id:
        raise ApiError("ref must look like 'source:sensor'")
    want = bool(body.get("ignored", True))

    def fn(data):
        src = next((x for x in data["sources"] if x["id"] == source_id), None)
        if src is None:
            raise ApiError(f"sensor source '{source_id}' not found", 404)
        lst = [x for x in src.get("ignored", []) if x != sensor_id]
        if want:
            lst.append(sensor_id)
        src["ignored"] = sorted(lst)
    save(request, fn)
    return ok()


async def test_source(request: Request) -> Response:
    s = svc(request)
    body = await body_json(request)
    cls = s.registry.get(body.get("type") or "")
    if cls is None:
        raise ApiError("unknown sensor type")
    if cls.push:
        return ok({"readings": [], "message": "Push sources receive data from your script; nothing to test here."})
    try:
        plugin = cls("test", body.get("options") or {})
        readings = await asyncio.wait_for(asyncio.to_thread(plugin.read), 20)
    except asyncio.TimeoutError:
        raise ApiError("the source did not answer within 20 s")
    except Exception as exc:
        raise ApiError(f"test failed: {exc}") from exc
    return ok({"readings": [
        {"id": k, "value": r.value, "status": r.status, "label": r.label, "message": r.message}
        for k, r in sorted(readings.items())
    ]})


async def push(request: Request) -> Response:
    s = svc(request)
    sid = request.path_params["source_id"]
    src = s.store.config.source(sid)
    if src is None or src.type != "push":
        raise ApiError("no push source with this id", 404)
    token = (src.options or {}).get("token") or ""
    if token and request.headers.get("x-openfan-token") != token:
        raise ApiError("invalid token", 401)
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApiError("body must be JSON, e.g. {\"cpu\": 52.5}") from exc
    try:
        n = s.sensors.push(sid, PushSensor.parse_payload(payload))
    except KeyError as exc:
        raise ApiError("source is disabled or not running", 409) from exc
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return ok({"accepted": n})


# -- controllers --------------------------------------------------------------------
def _controller_from(body: dict[str, Any], cid: str) -> dict[str, Any]:
    cls = DRIVERS.get(body.get("type") or "")
    if cls is None:
        raise ApiError(f"unknown controller type '{body.get('type')}'")
    options = dict(body.get("options") or {})
    try:
        cls(cid, options)
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return {"id": cid, "type": cls.type_name, "name": str(body.get("name") or cls.title)[:60],
            "enabled": bool(body.get("enabled", True)), "options": options}


async def create_controller(request: Request) -> Response:
    body = await body_json(request)
    existing = {c.id for c in svc(request).store.config.controllers}
    cid = slugify(str(body.get("name") or "controller"), existing)
    ctrl = _controller_from(body, cid)
    save(request, lambda data: data["controllers"].append(ctrl))
    return ok({"id": cid})


async def update_controller(request: Request) -> Response:
    cid = request.path_params["controller_id"]
    body = await body_json(request)

    def fn(data):
        for i, c in enumerate(data["controllers"]):
            if c["id"] == cid:
                data["controllers"][i] = _controller_from(body, cid)
                return
        raise ApiError("controller not found", 404)
    save(request, fn)
    return ok()


async def delete_controller(request: Request) -> Response:
    cid = request.path_params["controller_id"]

    def fn(data):
        before = len(data["controllers"])
        data["controllers"] = [c for c in data["controllers"] if c["id"] != cid]
        if len(data["controllers"]) == before:
            raise ApiError("controller not found", 404)
        data["fans"] = [f for f in data["fans"] if f["controller"] != cid]
    save(request, fn)
    return ok()


async def test_controller(request: Request) -> Response:
    body = await body_json(request)
    cls = DRIVERS.get(body.get("type") or "")
    if cls is None:
        raise ApiError("unknown controller type")

    def run():
        drv = cls("test", body.get("options") or {})
        try:
            info = drv.connect()
            return {"name": info.name, "fan_count": info.fan_count, "hardware": info.hardware,
                    "firmware": info.firmware, "target": drv.target}
        finally:
            drv.close()
    try:
        return ok(await asyncio.wait_for(asyncio.to_thread(run), 15))
    except asyncio.TimeoutError:
        raise ApiError("no answer within 15 s")
    except Exception as exc:
        raise ApiError(f"connection failed: {exc}") from exc


async def controller_fan_names(request: Request) -> Response:
    """Preview the fan names stored on a REST controller."""
    s = svc(request)
    cid = request.path_params["controller_id"]
    try:
        names = await s.engine.controllers.read_fan_names(cid)
    except Exception as exc:
        raise ApiError(f"could not read fan names: {exc}") from exc
    return ok({"names": {str(k): v for k, v in sorted(names.items())}})


async def import_fan_names(request: Request) -> Response:
    """Read fan names from a REST controller and save them as this service's fan names."""
    s = svc(request)
    cid = request.path_params["controller_id"]
    try:
        names = await s.engine.controllers.read_fan_names(cid)
    except Exception as exc:
        raise ApiError(f"could not read fan names: {exc}") from exc
    rt = s.engine.controllers.ctrls.get(cid)
    count = rt.info.fan_count if rt and rt.info else None
    names = {i: n for i, n in names.items() if count is None or i < count}
    if not names:
        raise ApiError("the controller reported no fan names")

    def fn(data):
        for idx, name in names.items():
            fan = next((f for f in data["fans"] if f["controller"] == cid and f["index"] == idx), None)
            if fan is None:
                data["fans"].append({"controller": cid, "index": idx, "name": name, "curve": None})
            else:
                fan["name"] = name
    save(request, fn)
    s.events.add("info", "config", f"Imported {len(names)} fan name(s) from controller '{cid}'")
    return ok({"imported": len(names), "names": {str(k): v for k, v in sorted(names.items())}})


# -- notifications / settings / auth -------------------------------------------
async def update_notifications(request: Request) -> Response:
    body = await body_json(request)
    urls = [u.strip() for u in body.get("urls", []) if isinstance(u, str) and u.strip()]

    def fn(data):
        n = data["notifications"]
        n["urls"] = urls
        for key in ("enabled", "on_failsafe", "on_recovery", "on_controller"):
            if key in body:
                n[key] = bool(body[key])
    save(request, fn)
    return ok()


async def test_notifications(request: Request) -> Response:
    s = svc(request)
    body = await body_json(request)
    urls = body.get("urls")
    if urls is not None:
        urls = [u.strip() for u in urls if isinstance(u, str) and u.strip()]
    good, msg = await s.notifier.send_now("OpenFAN: test notification",
                                         "If you can read this, notifications from OpenFAN Service work.", urls)
    if not good:
        raise ApiError(msg)
    return ok({"message": msg})


async def update_settings(request: Request) -> Response:
    body = await body_json(request)

    def fn(data):
        for k, v in body.items():
            if k in data["settings"]:
                data["settings"][k] = v
    save(request, fn)
    return ok()


async def update_auth(request: Request) -> Response:
    s = svc(request)
    if s.env_password:
        raise ApiError("the password is set with the OPENFAN_PASSWORD environment variable; change it there")
    body = await body_json(request)
    enabled = bool(body.get("enabled"))
    password = str(body.get("password") or "")
    if password and len(password) < 6:
        raise ApiError("use at least 6 characters")

    def fn(data):
        if password:
            data["auth"]["password_hash"] = hash_password(password)
        data["auth"]["enabled"] = enabled
    cfg = save(request, fn)
    resp = ok()
    if cfg.auth.enabled:
        _set_cookie(resp, s.signer.issue(cfg.auth.password_hash))
    return resp


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax")


async def login(request: Request) -> Response:
    s = svc(request)
    body = await body_json(request)
    cfg = s.store.config
    if not cfg.auth.enabled:
        return ok()
    if not verify_password(str(body.get("password") or ""), cfg.auth.password_hash):
        await asyncio.sleep(1.0)  # slow down guessing
        raise ApiError("wrong password", 401)
    resp = ok()
    _set_cookie(resp, s.signer.issue(cfg.auth.password_hash))
    return resp


async def logout(request: Request) -> Response:
    resp = ok()
    resp.delete_cookie(COOKIE)
    return resp


# -- backup / restore --------------------------------------------------------------
async def backup(request: Request) -> Response:
    text = svc(request).store.export_text()
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    return PlainTextResponse(text, media_type="application/x-yaml", headers={
        "Content-Disposition": f'attachment; filename="openfan-config-{stamp}.yaml"'})


async def restore(request: Request) -> Response:
    s = svc(request)
    raw = await request.body()
    if len(raw) > 2_000_000:
        raise ApiError("file too large")
    try:
        cfg = parse_config_text(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ApiError("the file is not text") from exc
    except ConfigError as exc:
        raise ApiError(f"this file cannot be restored: {exc}") from exc
    if s.env_password or not cfg.auth.password_hash:
        cfg = cfg.model_copy(update={"auth": s.store.config.auth})
    s.store.replace(cfg)
    s.events.add("warning", "config", "Configuration restored from an uploaded file")
    return ok()


async def list_backups(request: Request) -> Response:
    return ok(svc(request).store.list_backups())


async def get_backup(request: Request) -> Response:
    name = request.path_params["name"]
    try:
        text = svc(request).store.read_backup(name)
    except ConfigError as exc:
        raise ApiError(str(exc), 404) from exc
    return PlainTextResponse(text, media_type="application/x-yaml",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


async def restore_backup(request: Request) -> Response:
    s = svc(request)
    name = request.path_params["name"]
    try:
        cfg = parse_config_text(s.store.read_backup(name))
    except ConfigError as exc:
        raise ApiError(f"cannot restore {name}: {exc}") from exc
    if s.env_password:
        cfg = cfg.model_copy(update={"auth": s.store.config.auth})
    s.store.replace(cfg)
    s.events.add("warning", "config", f"Configuration restored from backup {name}")
    return ok()


# -- history / override ---------------------------------------------------------
async def history(request: Request) -> Response:
    s = svc(request)
    q = request.query_params
    try:
        hours = min(24 * 365, max(0.1, float(q.get("hours", "24"))))
    except ValueError:
        raise ApiError("hours must be a number")
    until = time.time()
    since = until - hours * 3600
    if q.get("fan"):
        key = q["fan"]
        keys = {n: f"fan:{key}:{n}" for n in ("temp", "pwm", "rpm", "fs")}
    elif q.get("sensor"):
        keys = {"value": f"sensor:{q['sensor']}"}
    else:
        raise ApiError("pass ?fan=<controller:index> or ?sensor=<source:sensor>")
    data = await s.history.query(list(keys.values()), since, until, int(q.get("points", "300")))
    return ok({"since": since, "until": until, "series": {n: data[k] for n, k in keys.items()}})


async def clear_events(request: Request) -> Response:
    """Clear the "Recent events" list (memory and history database)."""
    s = svc(request)
    s.events.clear()
    await s.history.clear_events()
    return ok()


async def set_override(request: Request) -> Response:
    body = await body_json(request)
    try:
        pwm = int(body.get("pwm", 100))
        minutes = float(body.get("minutes", 10))
    except (TypeError, ValueError):
        raise ApiError("pwm and minutes must be numbers")
    if not 0 <= pwm <= 100 or not 0 < minutes <= 24 * 60:
        raise ApiError("pwm must be 0..100 and minutes 1..1440")
    svc(request).engine.set_override(pwm, minutes)
    return ok()


async def clear_override(request: Request) -> Response:
    svc(request).engine.clear_override()
    return ok()


ROUTES = [
    Route("/api/health", health),
    Route("/api/state", state),
    Route("/api/config", get_config),
    Route("/api/meta", meta),
    Route("/api/serial-ports", serial_ports),
    Route("/api/curves", create_curve, methods=["POST"]),
    Route("/api/curves/{curve_id}", update_curve, methods=["PUT"]),
    Route("/api/curves/{curve_id}", delete_curve, methods=["DELETE"]),
    Route("/api/fans/{controller}/{index:int}", update_fan, methods=["PUT"]),
    Route("/api/sources", create_source, methods=["POST"]),
    Route("/api/sources/test", test_source, methods=["POST"]),
    Route("/api/sensors/ignore", ignore_sensor, methods=["POST"]),
    Route("/api/sources/{source_id}", update_source, methods=["PUT"]),
    Route("/api/sources/{source_id}", delete_source, methods=["DELETE"]),
    Route("/api/push/{source_id}", push, methods=["POST"]),
    Route("/api/controllers", create_controller, methods=["POST"]),
    Route("/api/controllers/test", test_controller, methods=["POST"]),
    Route("/api/controllers/{controller_id}/fan-names", controller_fan_names),
    Route("/api/controllers/{controller_id}/fan-names/import", import_fan_names, methods=["POST"]),
    Route("/api/controllers/{controller_id}", update_controller, methods=["PUT"]),
    Route("/api/controllers/{controller_id}", delete_controller, methods=["DELETE"]),
    Route("/api/notifications", update_notifications, methods=["PUT"]),
    Route("/api/notifications/test", test_notifications, methods=["POST"]),
    Route("/api/settings", update_settings, methods=["PUT"]),
    Route("/api/auth", update_auth, methods=["POST"]),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/backup", backup),
    Route("/api/restore", restore, methods=["POST"]),
    Route("/api/backups", list_backups),
    Route("/api/backups/{name}", get_backup),
    Route("/api/backups/{name}/restore", restore_backup, methods=["POST"]),
    Route("/api/history", history),
    Route("/api/events", clear_events, methods=["DELETE"]),
    Route("/api/override", set_override, methods=["POST"]),
    Route("/api/override", clear_override, methods=["DELETE"]),
]
