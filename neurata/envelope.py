"""neurata/envelope.py — proveniência best-effort do depósito."""
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def capture(origin: str = "manual", agent: "str | None" = None,
            session: "str | None" = None, cwd: "Path | None" = None) -> dict:
    if cwd is not None:
        resolved_cwd = Path(cwd)
    else:
        try:
            resolved_cwd = Path.cwd()
        except OSError:
            # cwd deletado sob o processo (rmdir concorrente) — não deixa a
            # proveniência best-effort derrubar o depósito.
            resolved_cwd = Path(".")
    try:
        host = socket.gethostname()
    except OSError:
        host = "unknown"
    env: dict = {
        "host": host,
        "cwd": str(resolved_cwd),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "origin": origin,
    }
    git = _git_context(resolved_cwd)
    if git:
        env["git"] = git
    if agent is not None:
        env["agent"] = agent
    if session is not None:
        env["session"] = session
    return env


def _git_context(cwd: Path) -> dict:
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse",
             "--show-toplevel", "HEAD", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2, check=False)
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
