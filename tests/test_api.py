import time

import pytest
from starlette.testclient import TestClient

from openfan_service.app import create_app
from openfan_service.env import Env


@pytest.fixture
def client(tmp_path):
    env = Env(config_dir=tmp_path, host="127.0.0.1", port=0, log_level="WARNING", demo=True, password=None,
              serial_port=None, controller_url=None, plugins_enabled=False)
    app = create_app(env, watchdog=False)
    with TestClient(app) as c:
        yield c


def wait_for(client, pred, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        st = client.get("/api/state").json()["data"]
        if pred(st):
            return st
        time.sleep(0.2)
    raise AssertionError("condition not reached")


def test_state_and_health(client):
    st = wait_for(client, lambda s: len(s["fans"]) == 10 and s["fans"][0]["state"] == "ok")
    assert len(st["fans"]) == 10
    assert client.get("/api/health").json()["ok"]
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.css").status_code == 200


def test_curve_crud_and_assignment(client):
    r = client.post("/api/curves", json={"name": "GPU", "sensors": ["demo:disk1"], "points": [[30, 20], [70, 100]],
                                         "fans": ["main:5", "main:6"]})
    assert r.json()["ok"], r.json()
    cid = r.json()["data"]["id"]
    assert cid == "gpu"
    cfg = client.get("/api/config").json()["data"]
    assert {f["index"] for f in cfg["fans"] if f["curve"] == "gpu"} == {5, 6}
    # moving fan 0 from hdd-cage to gpu
    r = client.put(f"/api/curves/{cid}", json={"name": "GPU", "sensors": ["demo:disk1"], "points": [[30, 20], [70, 100]],
                                                "fans": ["main:0", "main:5"]})
    assert r.json()["ok"]
    cfg = client.get("/api/config").json()["data"]
    assert next(f for f in cfg["fans"] if f["index"] == 0)["curve"] == "gpu"
    assert next(f for f in cfg["fans"] if f["index"] == 6)["curve"] is None
    # validation error comes back as a readable message
    r = client.put(f"/api/curves/{cid}", json={"name": "GPU", "points": [[30, 200]]})
    assert r.status_code == 400 and "out of range" in r.json()["error"]
    # deleting a curve in use -> its fans go fail-safe
    assert client.delete(f"/api/curves/{cid}").json()["ok"]
    st = wait_for(client, lambda s: s["fans"][0]["state"] == "failsafe")
    assert st["fans"][0]["pwm"] == 100


def test_fan_rename_and_unassign(client):
    assert client.put("/api/fans/main/3", json={"name": "Rear", "curve": "hdd-cage"}).json()["ok"]
    st = wait_for(client, lambda s: s["fans"][3]["name"] == "Rear" and s["fans"][3]["state"] == "ok")
    assert client.put("/api/fans/main/3", json={"curve": None}).json()["ok"]
    wait_for(client, lambda s: s["fans"][3]["state"] == "unmanaged")


def test_sources_and_push(client):
    r = client.post("/api/sources", json={"type": "push", "name": "Script", "id": "script", "options": {"token": "s3cret"}})
    assert r.json()["ok"]
    assert client.post("/api/push/script", json={"cpu": 51.5}).status_code == 401
    r = client.post("/api/push/script", json={"cpu": 51.5}, headers={"X-OpenFAN-Token": "s3cret"})
    assert r.json()["data"]["accepted"] == 1
    st = client.get("/api/state").json()["data"]
    assert any(s["ref"] == "script:cpu" and s["value"] == 51.5 for s in st["sensors"])
    # token is masked in config output and kept on edit
    cfg = client.get("/api/config").json()["data"]
    assert next(s for s in cfg["sources"] if s["id"] == "script")["options"]["token"] == "********"
    assert client.put("/api/sources/script", json={"name": "Script 2", "options": {"token": "********"}}).json()["ok"]
    assert client.post("/api/push/script", json={"cpu": 50}, headers={"X-OpenFAN-Token": "s3cret"}).json()["ok"]
    # bad options rejected
    r = client.post("/api/sources", json={"type": "unraid_disks", "options": {"spun_down": "x"}})
    assert r.status_code == 400
    # test endpoint
    r = client.post("/api/sources/test", json={"type": "demo", "options": {"count": 2}})
    assert len(r.json()["data"]["readings"]) == 2


def test_backup_restore(client):
    text = client.get("/api/backup").text
    assert "controllers:" in text
    client.post("/api/curves", json={"name": "Temp", "points": [[30, 30]]})
    assert client.post("/api/restore", content=text.encode()).json()["ok"]
    cfg = client.get("/api/config").json()["data"]
    assert not any(c["id"] == "temp" for c in cfg["curves"])
    r = client.post("/api/restore", content=b"curves: [[[")
    assert r.status_code == 400
    backups = client.get("/api/backups").json()["data"]
    assert backups
    assert client.post(f"/api/backups/{backups[0]['name']}/restore").json()["ok"]


def test_login_flow(client):
    assert client.post("/api/auth", json={"enabled": True, "password": "abc"}).status_code == 400
    r = client.post("/api/auth", json={"enabled": True, "password": "secret1"})
    assert r.json()["ok"]
    client.cookies.clear()
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "secret1"}).json()["ok"]
    assert client.get("/api/state").status_code == 200
    assert "password_hash" not in str(client.get("/api/config").json())


def test_override_and_history(client):
    assert client.post("/api/override", json={"pwm": 100, "minutes": 1}).json()["ok"]
    wait_for(client, lambda s: s["fans"][0]["state"] == "override")
    assert client.delete("/api/override").json()["ok"]
    r = client.get("/api/history?fan=main:0&hours=1").json()
    assert set(r["data"]["series"]) == {"temp", "pwm", "rpm", "fs"}
    assert client.get("/api/history").status_code == 400


def test_settings_validation(client):
    assert client.put("/api/settings", json={"stale_timeout": 1}).status_code == 400
    assert client.put("/api/settings", json={"stale_timeout": 90}).json()["ok"]
    assert client.get("/api/config").json()["data"]["settings"]["stale_timeout"] == 90


def test_fan_names_not_available_for_demo_controller(client):
    st = client.get("/api/state").json()["data"]
    assert st["controllers"][0]["supports_fan_names"] is False
    r = client.get("/api/controllers/main/fan-names")
    assert r.status_code == 400 and "REST" in r.json()["error"]
    assert client.post("/api/controllers/main/fan-names/import").status_code == 400


def test_import_fan_names_from_rest_controller(client):
    import subprocess, sys, socket, pathlib
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    script = pathlib.Path(__file__).parent.parent / "scripts" / "fake_openfan_rest.py"
    proc = subprocess.Popen([sys.executable, str(script), "--host", "127.0.0.1", "--port", str(port), "--quiet", "--fans", "4"])
    try:
        r = client.post("/api/controllers", json={"type": "openfan_rest", "name": "Remote",
                                                  "options": {"url": f"http://127.0.0.1:{port}"}})
        cid = r.json()["data"]["id"]
        wait_for(client, lambda s: any(c["id"] == cid and c["connected"] for c in s["controllers"]))
        st = client.get("/api/state").json()["data"]
        assert next(c for c in st["controllers"] if c["id"] == cid)["supports_fan_names"] is True
        preview = client.get(f"/api/controllers/{cid}/fan-names").json()["data"]["names"]
        assert preview["0"] == "Front intake 1" and len(preview) == 4
        r = client.post(f"/api/controllers/{cid}/fan-names/import").json()
        assert r["ok"] and r["data"]["imported"] == 4
        cfg = client.get("/api/config").json()["data"]
        names = {f["index"]: f["name"] for f in cfg["fans"] if f["controller"] == cid}
        assert names == {0: "Front intake 1", 1: "Front intake 2", 2: "Front intake 3", 3: "Rear exhaust"}
    finally:
        proc.terminate()
        proc.wait(5)


def test_web_files_are_revalidated(client):
    r = client.get("/assets/js/views/fans.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_version_in_api_and_footer(client):
    h = client.get("/api/health").json()
    assert h["version"].startswith("v") and "[" in h["version"]
    assert set(h["version_info"]) == {"version", "git_hash", "full", "source"}
    html = client.get("/").text
    assert "https://sasakaranovic.com" in html and "https://karanovicresearch.com" in html and 'id="app-version"' in html


def test_clear_events(client):
    import sqlite3
    wait_for(client, lambda s: len(s["events"]) > 0)
    assert client.delete("/api/events").json()["ok"]
    assert client.get("/api/state").json()["data"]["events"] == []
    db = sqlite3.connect(client.app.state.services.history.path)
    try:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0   # also gone after a restart
    finally:
        db.close()
