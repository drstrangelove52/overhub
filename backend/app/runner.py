"""The one place where OverHub runs host commands (docker, tailscale).

Tests replace `run` with a fake, so nothing else may call subprocess directly.
"""
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    returncode: int
    output: str  # stdout and stderr merged

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900) -> Result:
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return Result(124, (exc.output or "") + f"\nZeitüberschreitung nach {timeout} s")
    except FileNotFoundError:
        return Result(127, f"Befehl nicht gefunden: {cmd[0]}")
    return Result(proc.returncode, proc.stdout)
