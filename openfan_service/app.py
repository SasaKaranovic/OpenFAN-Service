"""Application wiring: builds the services, the web app and the safety watchdog."""

from __future__ import annotations

import contextlib
import logging
import os
import threading
import time
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .version import get_version
from .api import ROUTES, ApiError, Services
from .auth import COOKIE, SessionSigner, hash_password, verify_password
from .config_store import ConfigStore
from .controllers import ControllerManager
from .engine import Engine
from .env import Env, load_env, setup_logging
from .events import EventLog
from .history import History
from .notifier import Notifier
from .sensors import PluginRegistry
from .sensors.manager import SensorManager

log = logging.getLogger("openfan")

WEB_DIR = Path(__file__).parent / "web"
OPEN_PATHS = ("/api/health", "/api/login", "/api/logout", "/api/push/")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        services: Services | None = getattr(request.app.state, "services", None)
        path = request.url.path
        if services and path.startswith("/api/") and not path.startswith(OPEN_PATHS):
            auth = services.store.config.auth
            if auth.enabled and not services.signer.valid(request.cookies.get(COOKIE), auth.password_hash):
                return JSONResponse({"ok": False, "error": "login required", "login": True}, status_code=401)
        return await call_next(request)


async def api_error(request: Request, exc: ApiError) -> Response:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=exc.status)


async def server_error(request: Request, exc: Exception) -> Response:
    log.exception("Unhandled API error on %s", request.url.path)
    return JSONResponse({"ok": False, "error": f"internal error: {exc}"}, status_code=500)


class WebFiles(StaticFiles):
    """Static web UI files, always revalidated by the browser.

    Without this, browsers may keep using a cached copy of a changed JavaScript module
    after an update (ES module imports have no version query string). "no-cache" still
    allows caching but makes the browser check the ETag first, so updates show up on reload.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class Watchdog(threading.Thread):
    """Last line of defence: if the control loop stops cycling, exit the process.

    With ``restart: unless-stopped`` Docker then starts a fresh container, which
    puts every managed fan back under control (at 100 % until sensors report).
    """

    def __init__(self, engine: Engine, store: ConfigStore) -> None:
        super().__init__(name="watchdog", daemon=True)
        self.engine, self.store = engine, store
        self.stop_flag = threading.Event()
        self.started_at = time.monotonic()

    def run(self) -> None:
        while not self.stop_flag.wait(5):
            limit = max(60.0, self.store.config.settings.update_interval * 15)
            last = self.engine.last_cycle or self.started_at
            if time.monotonic() - last > limit:
                log.critical("Control loop has not run for %.0f s; exiting so Docker restarts the service",
                             time.monotonic() - last)
                logging.shutdown()
                os._exit(3)


def build_services(env: Env) -> Services:
    store = ConfigStore(env.config_dir, demo=env.demo, serial_port=env.serial_port,
                        controller_url=env.controller_url)
    if env.password:
        cfg = store.config
        if not (cfg.auth.enabled and verify_password(env.password, cfg.auth.password_hash)):
            store.mutate(lambda d: d["auth"].update(enabled=True, password_hash=hash_password(env.password)))
    registry = PluginRegistry()
    if env.plugins_enabled:
        registry.load_user_plugins(env.config_dir / "plugins")
    events = EventLog()
    history = History(env.config_dir / "history.sqlite", store.config.settings.history_retention_days)
    sensors = SensorManager(registry, store.config.settings.stale_timeout)
    controllers = ControllerManager()
    notifier = Notifier()
    engine = Engine(store, sensors, controllers, history, notifier, events)
    events.add_sink(history.add_event_nowait)
    notifier.on_error = lambda msg: events.add("warning", "notify", f"Notification failed: {msg}")
    store.on_change(engine.on_config_change)
    return Services(store=store, registry=registry, sensors=sensors, engine=engine, history=history,
                    notifier=notifier, events=events, signer=SessionSigner(env.config_dir),
                    env_password=bool(env.password))


def create_app(env: Env | None = None, *, watchdog: bool = True) -> Starlette:
    env = env or load_env()

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        services: Services = app.state.services
        await services.history.open()
        services.events.preload(await services.history.events(30))
        services.engine.apply_config(services.store.config)
        services.notifier.start()
        services.engine.start()
        services.events.add("info", "service", f"OpenFAN Service {get_version().full} started")
        dog = Watchdog(services.engine, services.store) if watchdog else None
        if dog:
            dog.start()
        try:
            yield
        finally:
            if dog:
                dog.stop_flag.set()
            services.events.add("info", "service", "OpenFAN Service stopping")
            await services.engine.stop()
            await services.sensors.stop()
            await services.notifier.stop()
            await services.history.close()

    async def index(request: Request) -> Response:
        return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    routes = [*ROUTES, Route("/", index), Mount("/", app=WebFiles(directory=WEB_DIR), name="web")]
    app = Starlette(
        routes=routes,
        middleware=[Middleware(AuthMiddleware)],
        exception_handlers={ApiError: api_error, Exception: server_error},
        lifespan=lifespan,
    )
    app.state.services = build_services(env)
    return app


def main() -> None:
    import uvicorn

    env = load_env()
    setup_logging(env.log_level)
    log.info("OpenFAN Service %s, config folder %s, web UI on port %d", get_version().full, env.config_dir, env.port)
    uvicorn.run(create_app(env), host=env.host, port=env.port, log_config=None, access_log=False,
                timeout_graceful_shutdown=5)
