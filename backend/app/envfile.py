"""Read and write the per-app .env that docker compose reads.

Values are written unquoted when they only contain safe characters, otherwise
single-quoted (compose takes single-quoted values literally, so a "$" in a
user setting is never interpolated). Values with a single quote or a newline
are rejected up front.
"""
import os
import re
from pathlib import Path

_SAFE = re.compile(r"^[A-Za-z0-9_./:@,+=-]*$")


def validate_value(value: str) -> None:
    if "\n" in value or "\r" in value or "'" in value:
        raise ValueError("Werte dürfen keine Zeilenumbrüche und kein ' enthalten")


def format_value(value: str) -> str:
    validate_value(value)
    return value if _SAFE.match(value) else f"'{value}'"


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        env[key.strip()] = value
    return env


def write_env(path: Path, env: dict[str, str]) -> None:
    """Write atomically with mode 0600 (it holds the app's secrets)."""
    content = "".join(f"{key}={format_value(value)}\n" for key, value in env.items())
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)
