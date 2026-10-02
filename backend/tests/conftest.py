import json
import os
import shutil
import tempfile
import time
from pathlib import Path

# Before any app import: settings and the SQLite engine are created at import time.
_DATA = Path(tempfile.mkdtemp(prefix="overhub-test-"))
os.environ["OVERHUB_DATA_DIR"] = str(_DATA)
os.environ["OVERHUB_SESSION_COOKIE_SECURE"] = "false"
os.environ["OVERHUB_ADMIN_PASSWORD"] = "admin-pass-123"
os.environ["OVERHUB_HEALTH_TIMEOUT_SECONDS"] = "5"

import pytest
from fastapi.testclient import TestClient

from app import runner
from app.catalog import load_catalog
from app.database import Base, engine
from app.main import app
from app.routers.auth import limiter

DNS = "pi.tail1234.ts.net"


class FakeHost:
    """Stands in for docker and tailscale. Records every command."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.digest_ok = True
        self.healthy = True
        self.pulled_version = None

    def run(self, cmd, cwd=None, timeout=900):
        self.calls.append(cmd)
        if cmd[:2] == ["tailscale", "status"]:
            return runner.Result(0, json.dumps({
                "BackendState": "Running",
                "Self": {"DNSName": DNS + ".", "KeyExpiry": "2027-03-31T00:00:00Z"},
                "CertDomains": [DNS],
            }))
        if cmd[:2] == ["tailscale", "serve"]:
            return runner.Result(0, "Serve started")
        if cmd[:3] == ["docker", "image", "inspect"]:
            image = cmd[3]
            repo = image.rsplit(":", 1)[0]
            manifest = next(m for m in load_catalog().values() if repo in m.images)
            digest = manifest.images[repo] if self.digest_ok else "sha256:" + "0" * 64
            return runner.Result(0, json.dumps([f"{repo}@{digest}"]))
        if cmd[:2] == ["docker", "compose"]:
            app_dir = Path(cmd[3])
            args = cmd[6:]
            env = dict(l.split("=", 1) for l in (app_dir / ".env").read_text().splitlines() if "=" in l)
            app_id = app_dir.name
            manifest = load_catalog()[app_id]
            if args[:2] == ["config", "--images"]:
                profiles = env.get("COMPOSE_PROFILES", "").split(",")
                imgs = [f"{repo}:{env['APP_VERSION']}" for repo in manifest.images
                        if not repo.endswith("-mcp") or "mcp" in profiles]
                return runner.Result(0, "\n".join(imgs + (["mariadb:11"] if app_id == "overcook" else [])))
            if args[:1] == ["pull"]:
                self.pulled_version = env["APP_VERSION"]
                return runner.Result(0, "")
            if args[:1] == ["ps"]:
                health = "healthy" if self.healthy else "unhealthy"
                return runner.Result(0, "\n".join(json.dumps({"Service": s, "State": "running", "Health": health})
                                                  for s in ("frontend", "backend")))
            if args[:1] == ["logs"]:
                return runner.Result(0, "backend-1 | hello")
            return runner.Result(0, "")
        return runner.Result(127, f"unexpected command {cmd}")


@pytest.fixture()
def host(monkeypatch):
    fake = FakeHost()
    monkeypatch.setattr(runner, "run", fake.run)

    class Resp:
        status_code = 200

    monkeypatch.setattr("app.operations.httpx.get", lambda *a, **k: Resp())
    monkeypatch.setattr("app.operations.time.sleep", lambda s: None)
    return fake


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    shutil.rmtree(_DATA / "apps", ignore_errors=True)
    limiter.reset()
    with TestClient(app) as test_client:  # lifespan creates tables + bootstrap admin
        yield test_client


@pytest.fixture()
def admin(client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    assert r.status_code == 200
    return client


def wait_job(client, job_id, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")
