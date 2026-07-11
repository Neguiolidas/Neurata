"""neurata/envelope.py — proveniência best-effort do depósito."""
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def capture(origin: str = "manual", agent: "str | None" = None,
            session: "str | None" = None, cwd: "Path | None" = None) -> dict:
    cwd = Path(cwd) if cwd is not None else Path.cwd()
    env: dict = {
        "host": socket.gethostname(),
        "cwd": str(cwd),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": origin,
    }
    git = _git_context(cwd)
    if git:
        env["git"] = git
    if agent:
        env["agent"] = agent
    if session:
        env["session"] = session
    return env


def _git_context(cwd: Path) -> dict:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse",
             "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0:
        return {}
    lines = proc.stdout.strip().splitlines()
    if len(lines) < 3:
        # repo sem commit: --show-toplevel funciona, HEAD falha junto no rc,
        # mas alguns gits devolvem só o toplevel — trata como repo válido.
        return {"root": str(Path(lines[0]).resolve())} if lines else {}
    return {"root": str(Path(lines[0]).resolve()),
            "commit": lines[1][:12], "branch": lines[2]}
