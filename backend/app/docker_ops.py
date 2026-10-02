"""docker compose for one installed app (project dir = /opt/overhub/apps/<id>)."""
import json
from dataclasses import dataclass
from pathlib import Path

from app import runner


@dataclass
class ServiceState:
    service: str
    state: str  # running, exited, restarting, ...
    health: str  # healthy, unhealthy, starting, or "" without a healthcheck


def compose(app_dir: Path, *args: str, timeout: int = 900, merge_stderr: bool = True,
            stdin_path: Path | None = None, stdout_path: Path | None = None) -> runner.Result:
    return runner.run(
        ["docker", "compose", "--project-directory", str(app_dir), "-f", str(app_dir / "compose.yml"), *args],
        timeout=timeout,
        merge_stderr=merge_stderr,
        stdin_path=stdin_path,
        stdout_path=stdout_path,
    )


def images(app_dir: Path) -> list[str]:
    """Images the app actually uses with its current .env (version, profiles)."""
    result = compose(app_dir, "config", "--images", merge_stderr=False)
    return [line.strip() for line in result.output.splitlines() if line.strip()] if result.ok else []


def repo_digests(image: str) -> list[str]:
    result = runner.run(["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"], merge_stderr=False)
    if not result.ok:
        return []
    try:
        return json.loads(result.output.strip() or "[]") or []
    except json.JSONDecodeError:
        return []


def verify_digests(app_dir: Path, expected: dict[str, str]) -> list[str]:
    """Problems for every catalog image whose pulled digest differs from the manifest."""
    problems = []
    for image in images(app_dir):
        repo = image.rsplit(":", 1)[0] if ":" in image.split("/")[-1] else image
        if repo not in expected:
            continue  # third-party base images (mariadb) are not pinned by the catalog
        want = f"{repo}@{expected[repo]}"
        if want not in repo_digests(image):
            problems.append(f"{image}: Digest stimmt nicht mit dem Katalog überein (erwartet {expected[repo]})")
    return problems


def ps(app_dir: Path) -> list[ServiceState]:
    result = compose(app_dir, "ps", "--all", "--format", "json", merge_stderr=False)
    if not result.ok:
        return []
    text = result.output.strip()
    if not text:
        return []
    # Newer compose prints one JSON object per line, older ones a JSON array.
    rows = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l.strip()]
    return [ServiceState(r.get("Service", ""), r.get("State", ""), r.get("Health", "")) for r in rows]


def all_healthy(states: list[ServiceState]) -> bool:
    return bool(states) and all(s.state == "running" and s.health in ("healthy", "") for s in states)


def logs(app_dir: Path, service: str | None = None, tail: int = 200) -> str:
    args = ["logs", "--no-color", "--timestamps", "--tail", str(tail)]
    if service:
        args.append(service)
    return compose(app_dir, *args, timeout=60).output
