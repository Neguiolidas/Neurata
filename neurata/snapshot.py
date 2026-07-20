"""neurata/snapshot.py — git audit da library (plumbing best-effort).

Espelha o padrão de `envelope.py`: subprocess puro (sem `import git`/
dulwich), timeout, catch `(OSError, subprocess.TimeoutExpired)`. Repo git
vive dentro de `home.library` (repo root = library dir); `git -C
<library>` sempre, nunca `chdir`.
"""
import subprocess

GIT_TIMEOUT = 15  # commit/checkout local; push tem timeout próprio maior

_AVAIL: "bool | None" = None


class SnapshotError(RuntimeError):
    pass


def _run(home, *args, timeout=GIT_TIMEOUT, check=False) -> subprocess.CompletedProcess:
    cmd = ["git", "-C", str(home.library), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if check:
            raise SnapshotError(str(exc)) from exc
        return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr=str(exc))
    if check and proc.returncode != 0:
        raise SnapshotError(proc.stderr)
    return proc


def git_available() -> bool:
    global _AVAIL
    if _AVAIL is None:
        try:
            proc = subprocess.run(["git", "--version"], capture_output=True,
                                   text=True, timeout=GIT_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            _AVAIL = False
        else:
            _AVAIL = proc.returncode == 0
    return _AVAIL


def ensure_repo(home) -> bool:
    if not git_available():
        return False
    if (home.library / ".git").exists():
        return True
    _run(home, "init", "-q", "-b", "main")
    _run(home, "config", "user.name", "neurata")
    _run(home, "config", "user.email", "neurata@localhost")
    _run(home, "config", "commit.gpgsign", "false")
    _run(home, "config", "core.autocrlf", "false")
    _run(home, "config", "core.hooksPath", "/dev/null")
    return True


def has_changes(home) -> bool:
    if not git_available():
        return False
    proc = _run(home, "status", "--porcelain")
    return bool(proc.stdout.strip())


def commit(home, subject, body="") -> "str | None":
    if not git_available():
        return None
    _run(home, "add", "-A")
    if not has_changes(home):
        return None
    args = ["commit", "-m", subject]
    if body:
        args += ["-m", body]
    _run(home, *args, check=True)
    proc = _run(home, "rev-parse", "--short=12", "HEAD", check=True)
    return proc.stdout.strip()


def set_remote(home, url: str) -> None:
    proc = _run(home, "remote", "get-url", "neurata")
    if proc.returncode == 0:
        _run(home, "remote", "set-url", "neurata", url)
    else:
        _run(home, "remote", "add", "neurata", url)
