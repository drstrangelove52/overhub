import os
import sys

from app.catalog import load_catalog
from app.config import settings
from app.envfile import read_env
from tests.conftest import wait_job


def test_health_and_version_without_login(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert client.get("/api/version").json() == {"version": "dev"}


def test_api_requires_login(client):
    assert client.get("/api/apps").status_code == 401
    assert client.post("/api/apps/overstand/install", json={}).status_code == 401


def test_login_logout_and_wrong_password(client):
    assert client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"}).status_code == 200
    assert client.get("/api/auth/me").json() == {"username": "admin"}
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_change_password(admin):
    r = admin.put("/api/auth/me/password", json={"current_password": "admin-pass-123", "new_password": "neu-geheim"})
    assert r.status_code == 200
    admin.post("/api/auth/logout")
    assert admin.post("/api/auth/login", json={"username": "admin", "password": "neu-geheim"}).status_code == 200


def test_catalog_listing(admin, host):
    apps = {a["id"]: a for a in admin.get("/api/apps").json()}
    assert set(apps) == {"overcook", "overstand"}
    assert apps["overcook"]["installed"] is False
    assert "mcp" in apps["overcook"]["components"]


def test_install_overcook_with_mcp(admin, host):
    r = admin.post("/api/apps/overcook/install",
                   json={"settings": {"ADMIN_USERNAME": "martin"}, "components": ["mcp"]})
    job = wait_job(admin, r.json()["job_id"])
    assert job["status"] == "success", job["log"]

    env = read_env(settings.apps_dir / "overcook" / ".env")
    assert env["COMPOSE_PROFILES"] == "mcp"
    if sys.platform != "win32":
        assert oct(os.stat(settings.apps_dir / "overcook" / ".env").st_mode & 0o777) == "0o600"
    assert job["credentials"]["ADMIN_PASSWORD"] == env["ADMIN_PASSWORD"]
    assert job["credentials"]["url"] == "https://pi.tail1234.ts.net:8443"
    # show-once: gone on the second read
    assert admin.get(f"/api/jobs/{job['id']}").json()["credentials"] is None

    assert ["tailscale", "serve", "--bg", "--https=8443", "http://127.0.0.1:18443"] in host.calls
    apps = {a["id"]: a for a in admin.get("/api/apps").json()}
    assert apps["overcook"]["installed"] and apps["overcook"]["healthy"]
    assert apps["overcook"]["enabled_components"] == ["mcp"]


def test_install_twice_is_refused(admin, host):
    wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    job = wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    assert job["status"] == "failed" and "bereits installiert" in job["log"]


def test_install_fails_on_digest_mismatch(admin, host):
    host.digest_ok = False
    job = wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    assert job["status"] == "failed"
    assert "Digest stimmt nicht" in job["log"]
    assert not any(c[:2] == ["tailscale", "serve"] for c in host.calls)
    assert not admin.get("/api/apps").json()[1]["installed"]


def test_install_fails_when_never_healthy(admin, host):
    host.healthy = False
    job = wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"], timeout=15)
    assert job["status"] == "failed" and "nicht innerhalb" in job["log"]


def test_install_rejects_unknown_component_and_setting(admin, host):
    assert admin.post("/api/apps/overstand/install", json={"components": ["x"]}).status_code == 400
    r = admin.post("/api/apps/overcook/install", json={"settings": {"ADMIN_USERNAME": "it's"}})
    assert r.status_code == 400


def test_update_to_newer_catalog_version(admin, host, monkeypatch):
    wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    env_before = read_env(settings.apps_dir / "overstand" / ".env")

    job = wait_job(admin, admin.post("/api/apps/overstand/update").json()["job_id"])
    assert job["status"] == "failed" and "bereits aktuell" in job["log"]

    manifest = load_catalog()["overstand"]
    monkeypatch.setattr(manifest, "version", "0.2.0")
    job = wait_job(admin, admin.post("/api/apps/overstand/update").json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert host.pulled_version == "0.2.0"
    env_after = read_env(settings.apps_dir / "overstand" / ".env")
    assert env_after["APP_VERSION"] == "0.2.0" and env_after["TZ"] == env_before["TZ"]
    app = next(a for a in admin.get("/api/apps").json() if a["id"] == "overstand")
    assert app["version"] == "0.2.0" and not app["update_available"]


def test_logs(admin, host):
    assert admin.get("/api/apps/overstand/logs").status_code == 404
    wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    assert "hello" in admin.get("/api/apps/overstand/logs").json()["logs"]


def test_system(admin, host):
    data = admin.get("/api/system").json()
    assert data["tailscale"]["dns_name"] == "pi.tail1234.ts.net"
    assert data["tailscale"]["key_expiry"].startswith("2027")


def test_uninstall_keep_data_then_reinstall_reuses_secrets(admin, host):
    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    secret = read_env(settings.apps_dir / "overcook" / ".env")["MYSQL_PASSWORD"]

    job = wait_job(admin, admin.post("/api/apps/overcook/uninstall", json={}).json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert ["tailscale", "serve", "--https=8443", "off"] in host.calls
    down = [c for c in host.calls if "down" in c][-1]
    assert "--volumes" not in down
    app = next(a for a in admin.get("/api/apps").json() if a["id"] == "overcook")
    assert not app["installed"] and app["data_kept"]

    wait_job(admin, admin.post("/api/apps/overcook/install", json={}).json()["job_id"])
    assert read_env(settings.apps_dir / "overcook" / ".env")["MYSQL_PASSWORD"] == secret


def test_uninstall_delete_data(admin, host):
    wait_job(admin, admin.post("/api/apps/overstand/install", json={}).json()["job_id"])
    job = wait_job(admin, admin.post("/api/apps/overstand/uninstall", json={"delete_data": True}).json()["job_id"])
    assert job["status"] == "success", job["log"]
    assert "--volumes" in [c for c in host.calls if "down" in c][-1]
    assert not (settings.apps_dir / "overstand").exists()
    app = next(a for a in admin.get("/api/apps").json() if a["id"] == "overstand")
    assert not app["installed"] and not app["data_kept"]


def test_uninstall_not_installed(admin, host):
    job = wait_job(admin, admin.post("/api/apps/overstand/uninstall", json={}).json()["job_id"])
    assert job["status"] == "failed" and "nicht installiert" in job["log"]
