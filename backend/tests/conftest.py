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
os.environ["OVERHUB_SCHEDULER_ENABLED"] = "false"
# pytest's tmp_path dirs live below the system temp dir and are no mounts
os.environ["OVERHUB_BACKUP_ROOT"] = tempfile.gettempdir()
os.environ["OVERHUB_REQUIRE_MOUNTED_TARGETS"] = "false"

import pytest
from fastapi.testclient import TestClient

from app import runner
from app.catalog import load_catalog


def backup_exports_dir():
    from app import backup
    return backup.exports_dir()
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
        self.unhealthy_version = None  # this APP_VERSION never turns healthy
        self.pulled_version = None
        self.restic_fail = False
        self.repos: dict[str, str] = {}  # repo -> password it was created with
        self.snapshots: list[tuple[str, str]] = []  # (repo, staged path)
        self.snapshot_meta: dict[str, dict] = {}  # snapshot id -> staged meta.json
        self.snapshot_repo: dict[str, str] = {}
        self.snapshot_files: dict[str, dict[str, bytes]] = {}  # snapshot id -> staged files
        self.packed: dict[str, str] = {}  # export file -> password of the packed repo
        self.restic_env: list[dict] = []
        self.sftp_password = "nas-pass-123"  # what the fake NAS accepts
        self.sftp_up = True

    def run(self, cmd, cwd=None, timeout=900, merge_stderr=True, env=None, stdin_path=None, stdout_path=None):
        self.calls.append(cmd)
        if cmd[0] == "tar":
            if cmd[1] == "-cf":  # pack an export repo
                Path(cmd[2]).write_bytes(b"fake export")
                self.packed[cmd[2]] = self.repos[str(Path(cmd[4]))]
            else:  # tar -xf <upload> -C <repo>
                upload, repo = cmd[2], str(Path(cmd[4]))
                source = next((pw for f, pw in self.packed.items() if Path(f).read_bytes() == Path(upload).read_bytes()), None)
                if source is None:
                    return runner.Result(2, "tar: not a tar archive")
                self.repos[repo] = source
                for sid, r in list(self.snapshot_repo.items()):
                    if Path(r).parent == backup_exports_dir():
                        self.snapshot_repo[sid + "x"] = repo
                        self.snapshot_meta[sid + "x"] = self.snapshot_meta[sid]
                        self.snapshot_files[sid + "x"] = self.snapshot_files[sid]
            return runner.Result(0, "")
        if cmd[0] == "sshpass":  # sftp login check against the fake NAS
            if not self.sftp_up:
                return runner.Result(255, "ssh: connect to host nas.local port 22: Connection refused\nConnection closed")
            return runner.Result(0 if (env or {}).get("SSHPASS") == self.sftp_password else 5, "")
        if cmd[0] == "restic":
            self.restic_env.append(env or {})
            repo, args = cmd[2], cmd[3:]
            if "-o" in args:  # sftp.command for NAS targets
                args = args[:args.index("-o")]
                if (env or {}).get("SSHPASS") != self.sftp_password:
                    return runner.Result(1, "", "Fatal: unable to open repository: ssh exited")
            password = (env or {}).get("RESTIC_PASSWORD")
            if self.restic_fail:
                return runner.Result(1, "", "Fatal: unable to open repository")
            if args[:1] == ["init"]:
                self.repos[repo] = password
                if not repo.startswith("sftp:"):
                    Path(repo).mkdir(parents=True, exist_ok=True)
                    (Path(repo) / "config").write_text("fake")
                return runner.Result(0, "created restic repository")
            if repo not in self.repos:
                return runner.Result(1, "", "Fatal: repository does not exist")
            if self.repos[repo] != password:
                return runner.Result(1, "", "Fatal: wrong password or no key found")
            if args[:2] == ["cat", "config"]:
                return runner.Result(0, "{}")
            if args[:1] == ["backup"]:
                staged = Path(args[1])
                if (staged / "dump.sql").exists():
                    assert (staged / "dump.sql").read_text() == "-- fake dump\n"
                self.snapshots.append((repo, str(staged)))
                sid = f"{len(self.snapshots):08x}"
                self.snapshot_meta[sid] = json.loads((staged / "meta.json").read_text())
                self.snapshot_repo[sid] = repo
                self.snapshot_files[sid] = {
                    str(f.relative_to(staged)).replace("\\", "/"): f.read_bytes()
                    for f in staged.rglob("*") if f.is_file()
                }
                return runner.Result(0, json.dumps({"message_type": "status"}) + "\n"
                                     + json.dumps({"message_type": "summary", "snapshot_id": sid}))
            if args[:1] == ["snapshots"]:
                app_tag = args[args.index("--tag") + 1] if "--tag" in args else None
                snaps = [
                    {"id": sid, "short_id": sid, "time": f"2026-10-02T20:{i:02d}:00Z",
                     "tags": [f"app:{m.get('app')}", "reason:manual"]}
                    for i, (sid, m) in enumerate(self.snapshot_meta.items())
                    if self.snapshot_repo.get(sid) == repo and (app_tag is None or app_tag == f"app:{m.get('app')}")
                ]
                return runner.Result(0, json.dumps(snaps))
            if args[:1] == ["restore"]:
                sid = args[1].split(":", 1)[0]
                target = Path(args[args.index("--target") + 1])
                (target / "volumes").mkdir(parents=True, exist_ok=True)
                for rel, data in self.snapshot_files.get(sid, {}).items():
                    (target / rel).parent.mkdir(parents=True, exist_ok=True)
                    (target / rel).write_bytes(data)
                return runner.Result(0, "restoring")
            if args[:1] == ["dump"]:
                sid, path = args[1], Path(args[2])
                return runner.Result(0, self.snapshot_files.get(sid, {}).get(path.name, b"").decode())
            return runner.Result(0, "[]")  # forget
        if cmd[:3] == ["docker", "network", "inspect"]:
            return runner.Result(0, "172.17.0.1")
        if cmd[:2] == ["docker", "run"]:
            return runner.Result(0, "")  # volume tar in/out
        if stdout_path is not None:
            Path(stdout_path).write_text("-- fake dump\n")
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
                ok = self.healthy and env.get("APP_VERSION") != self.unhealthy_version
                services = ("frontend", "backend", "db") if app_id == "overcook" else ("frontend",)
                return runner.Result(0, "\n".join(
                    json.dumps({"Service": s, "State": "running", "Health": "healthy" if ok or s == "db" else "unhealthy"})
                    for s in services))
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
    from app import backup
    backup._available_cache.clear()
    for sub in ("apps", "safety", "staging", "exports", "imports", "restore", "cache"):
        shutil.rmtree(_DATA / sub, ignore_errors=True)
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
