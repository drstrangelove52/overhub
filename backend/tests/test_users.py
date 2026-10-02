from app.config import settings
from app.envfile import read_env
from tests.conftest import wait_job


def _login(client, username, password):
    client.cookies.clear()
    return client.post("/api/auth/login", json={"username": username, "password": password})


def _install(admin, app_id="overcook"):
    job = wait_job(admin, admin.post(f"/api/apps/{app_id}/install", json={}).json()["job_id"])
    assert job["status"] == "success", job["log"]


def test_admin_gets_admin_role_on_installed_apps(admin, host):
    _install(admin)
    me = next(u for u in admin.get("/api/users").json() if u["username"] == "admin")
    assert me["roles"] == {"_overhub": "admin", "overcook": "admin"}


def test_app_env_knows_overhub(admin, host):
    _install(admin)
    env = read_env(settings.apps_dir / "overcook" / ".env")
    assert env["OVERHUB_URL"] == "http://172.17.0.1:10444"  # reachable from the app's container
    assert env["OVERHUB_PUBLIC_URL"] == "https://pi.tail1234.ts.net"


def test_family_member_uses_apps_but_cannot_manage(admin, host):
    _install(admin)
    _install(admin, "overstand")
    r = admin.post("/api/users", json={"username": "Tanja", "password": "tanja-pass-1", "roles": {"overcook": "user"}})
    assert r.status_code == 200 and r.json()["username"] == "tanja"
    assert admin.post("/api/users", json={"username": "tanja", "password": "xxxxxxxx1"}).status_code == 409

    assert _login(admin, "tanja", "tanja-pass-1").status_code == 200
    assert admin.get("/api/auth/me").json() == {"username": "tanja", "is_admin": False}
    for path in ("/api/apps", "/api/backup", "/api/system", "/api/users"):
        assert admin.get(path).status_code == 403, path
    mine = admin.get("/api/my-apps").json()
    assert [a["id"] for a in mine] == ["overcook"]
    assert mine[0]["url"] == "https://pi.tail1234.ts.net:8443" and mine[0]["role"] == "user"

    # what OverCook's backend asks with the browser's cookie
    assert admin.get("/api/sso/whoami?app=overcook").json() == {"username": "tanja", "role": "user"}
    assert admin.get("/api/sso/whoami?app=overstand").status_code == 403
    admin.post("/api/auth/logout")
    assert admin.get("/api/sso/whoami?app=overcook").status_code == 401


def test_roles_change_and_last_admin_is_protected(admin, host):
    _install(admin)
    users = admin.get("/api/users").json()
    me = users[0]
    r = admin.put(f"/api/users/{me['id']}", json={"roles": {"_overhub": None}})
    assert r.status_code == 400 and "mindestens einen" in r.json()["detail"]
    assert admin.delete(f"/api/users/{me['id']}").status_code == 400  # not yourself

    tanja = admin.post("/api/users", json={"username": "tanja", "password": "tanja-pass-1"}).json()
    assert tanja["roles"] == {}
    r = admin.put(f"/api/users/{tanja['id']}", json={"roles": {"overcook": "admin", "_overhub": "admin"}})
    assert r.json()["roles"] == {"overcook": "admin", "_overhub": "admin"}
    assert admin.put(f"/api/users/{tanja['id']}", json={"roles": {"overcook": "chef"}}).status_code == 400
    assert admin.put(f"/api/users/{tanja['id']}", json={"roles": {"gibtsnicht": "user"}}).status_code == 400


def test_password_reset_ends_sessions(admin, host):
    tanja = admin.post("/api/users", json={"username": "tanja", "password": "tanja-pass-1"}).json()
    admin_cookie = admin.cookies.get(settings.session_cookie_name)
    _login(admin, "tanja", "tanja-pass-1")
    tanja_cookie = admin.cookies.get(settings.session_cookie_name)
    admin.cookies.set(settings.session_cookie_name, admin_cookie)
    assert admin.put(f"/api/users/{tanja['id']}", json={"password": "neu-gesetzt-1"}).status_code == 200
    admin.cookies.set(settings.session_cookie_name, tanja_cookie)
    assert admin.get("/api/auth/me").status_code == 401
    assert _login(admin, "tanja", "neu-gesetzt-1").status_code == 200


def test_uninstall_with_delete_removes_app_roles(admin, host):
    _install(admin)
    wait_job(admin, admin.post("/api/apps/overcook/uninstall", json={"delete_data": True}).json()["job_id"])
    me = admin.get("/api/users").json()[0]
    assert "overcook" not in me["roles"]


def test_session_is_extended_when_used(admin, host):
    from datetime import datetime, timedelta, timezone

    from app.database import SessionLocal
    from app.models import Session

    db = SessionLocal()
    try:
        session = db.query(Session).first()
        session.expires_at = datetime.now(timezone.utc) + timedelta(days=3)
        db.commit()
    finally:
        db.close()
    assert admin.get("/api/auth/me").status_code == 200  # any authenticated call extends
    db = SessionLocal()
    try:
        expires = db.query(Session).first().expires_at.replace(tzinfo=timezone.utc)
        assert expires > datetime.now(timezone.utc) + timedelta(days=89)
    finally:
        db.close()


def test_existing_apps_get_the_bridge_url(admin, host):
    from app import sso_server
    from app.envfile import write_env

    _install(admin)
    path = settings.apps_dir / "overcook" / ".env"
    env = read_env(path)
    env["OVERHUB_URL"] = "http://127.0.0.1:10443"  # as written by OverHub 0.4.0
    write_env(path, env)
    assert sso_server.sync_app_env(lambda m: None) == ["overcook"]
    assert read_env(path)["OVERHUB_URL"] == "http://172.17.0.1:10444"
    assert any(c[6:8] == ["up", "-d"] for c in host.calls[-3:])
    assert sso_server.sync_app_env(lambda m: None) == []  # nothing to do the second time


def test_sso_listener_serves_only_whoami():
    from fastapi.testclient import TestClient

    from app.sso_server import sso_app

    c = TestClient(sso_app)
    assert c.get("/api/sso/whoami?app=overcook").status_code == 401
    assert c.get("/api/apps").status_code == 404
    assert c.get("/api/users").status_code == 404
