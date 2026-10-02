"""tailscale CLI against the host's tailscaled (socket mounted into the container)."""
import json

from app import runner


def status() -> dict:
    result = runner.run(["tailscale", "status", "--json"], timeout=30)
    if not result.ok:
        return {}
    try:
        return json.loads(result.output)
    except json.JSONDecodeError:
        return {}


def dns_name(st: dict | None = None) -> str | None:
    st = status() if st is None else st
    name = (st.get("Self") or {}).get("DNSName") or ""
    return name.rstrip(".") or None


def app_url(port: int, st: dict | None = None) -> str | None:
    name = dns_name(st)
    if not name:
        return None
    return f"https://{name}" if port == 443 else f"https://{name}:{port}"


def serve(port: int, internal_port: int) -> runner.Result:
    return runner.run(
        ["tailscale", "serve", "--bg", f"--https={port}", f"http://127.0.0.1:{internal_port}"], timeout=60
    )


def serve_off(port: int) -> runner.Result:
    return runner.run(["tailscale", "serve", f"--https={port}", "off"], timeout=60)
