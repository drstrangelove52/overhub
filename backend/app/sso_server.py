"""Second listener for the apps' single sign-on check (contract rule 8).

OverHub serves its UI on the host's 127.0.0.1:10443 (network_mode: host). The
apps run in their own docker networks, where 127.0.0.1 is the container
itself — they cannot reach that. So /api/sso/whoami (and only that) is also
served on the docker bridge gateway (usually 172.17.0.1:10444): every app
container can reach the host there, other machines cannot.
"""
import contextlib
import threading

import uvicorn
from fastapi import FastAPI

from app import runner
from app.config import settings
from app.envfile import read_env, write_env
from app.routers.users import sso_router

SSO_PORT = 10444

sso_app = FastAPI(title="OverHub SSO", docs_url=None, redoc_url=None, openapi_url=None)
sso_app.include_router(sso_router)

_gateway: str | None = None


def bridge_gateway() -> str | None:
    """IPv4 of docker0 as the app containers see the host."""
    global _gateway
    if _gateway is None:
        result = runner.run(
            ["docker", "network", "inspect", "bridge", "--format", "{{(index .IPAM.Config 0).Gateway}}"],
            timeout=30, merge_stderr=False,
        )
        value = result.output.strip() if result.ok else ""
        _gateway = value if value and value.count(".") == 3 else ""
    return _gateway or None


def app_url() -> str:
    """OVERHUB_URL for the apps' .env."""
    gateway = bridge_gateway()
    return f"http://{gateway}:{SSO_PORT}" if gateway else "http://127.0.0.1:10443"


class _ThreadServer(uvicorn.Server):
    # Runs next to the main server: leave signal handling to that one.
    def capture_signals(self):
        return contextlib.nullcontext()

    def install_signal_handlers(self):  # uvicorn < 0.29
        pass


def start() -> None:
    gateway = bridge_gateway()
    if not gateway:
        print("sso: docker bridge gateway not found — apps cannot use single sign-on")
        return
    server = _ThreadServer(uvicorn.Config(sso_app, host=gateway, port=SSO_PORT, log_level="warning"))
    threading.Thread(target=server.run, name="sso-server", daemon=True).start()
    print(f"sso: listening on {gateway}:{SSO_PORT} for the apps")


def sync_app_env(log=print) -> list[str]:
    """Bring settings OverHub hands to the apps up to date: OVERHUB_URL on the
    bridge listener (installs made with OverHub 0.4.0 got 127.0.0.1,
    unreachable from containers) and TZ (follows the host, see install.sh).
    Restarts an app only when a value actually changes."""
    from app import docker_ops  # late import: avoids a cycle via operations

    wanted = {"OVERHUB_URL": app_url(), "TZ": settings.tz}
    changed = []
    for env_file in sorted(settings.apps_dir.glob("*/.env")):
        env = read_env(env_file)
        diff = {k: v for k, v in wanted.items() if k in env and env[k] != v}
        if diff:
            env.update(diff)
            write_env(env_file, env)
            docker_ops.compose(env_file.parent, "up", "-d")  # recreates the containers with the new env
            changed.append(env_file.parent.name)
            log(f"app env: {env_file.parent.name} now has {', '.join(f'{k}={v}' for k, v in diff.items())}")
    return changed
