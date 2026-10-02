"""The one place where OverHub runs host commands (docker, tailscale).

Tests replace `run` with a fake, so nothing else may call subprocess directly.
"""
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Result:
    returncode: int
    output: str  # stdout, plus stderr when merged
    stderr: str = ""  # only filled when not merged

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 900,
    merge_stderr: bool = True,
    env: dict[str, str] | None = None,
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
) -> Result:
    """merge_stderr=False for commands whose stdout gets parsed: the CLIs print
    warnings to stderr (e.g. tailscale "client version != tailscaled server
    version" when the CLI in the image is newer than the host's daemon), which
    would otherwise corrupt the JSON.

    stdin_path/stdout_path stream from/to a file (database dumps can be large);
    with stdout_path, `output` holds stderr. `env` is added to the environment
    (e.g. RESTIC_PASSWORD) without ever appearing on the command line.
    """
    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    stdout = open(stdout_path, "wb") if stdout_path else subprocess.PIPE
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            stdin=stdin,
            stdout=stdout,
            stderr=subprocess.PIPE if (stdout_path or not merge_stderr) else subprocess.STDOUT,
            env={**os.environ, **env} if env else None,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.output.decode(errors="replace") if isinstance(exc.output, bytes) else (exc.output or "")
        return Result(124, partial + f"\nZeitüberschreitung nach {timeout} s")
    except FileNotFoundError:
        return Result(127, f"Befehl nicht gefunden: {cmd[0]}")
    finally:
        for f in (stdin, stdout):
            if hasattr(f, "close"):
                f.close()
    out = proc.stdout.decode(errors="replace") if proc.stdout is not None else ""
    err = proc.stderr.decode(errors="replace") if proc.stderr is not None else ""
    if stdout_path:
        return Result(proc.returncode, err)
    return Result(proc.returncode, out, err)
