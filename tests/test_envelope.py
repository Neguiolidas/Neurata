"""tests/test_envelope.py"""
import subprocess
from unittest.mock import patch

from neurata.envelope import _git_context, capture


def test_always_present_fields(tmp_path):
    env = capture(cwd=tmp_path)
    assert env["host"]
    assert env["cwd"] == str(tmp_path)
    assert env["ts"].endswith("+00:00")
    assert env["origin"] == "manual"
    assert "agent" not in env and "session" not in env


def test_optional_agent_session(tmp_path):
    env = capture(agent="claude", session="s1", cwd=tmp_path)
    assert env["agent"] == "claude"
    assert env["session"] == "s1"


def test_git_context_inside_repo(tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)],
                   check=True, capture_output=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   check=True, capture_output=True)
    env = capture(cwd=tmp_path)
    assert env["git"]["branch"] == "main"
    assert env["git"]["root"] == str(tmp_path.resolve())
    assert len(env["git"]["commit"]) == 12


def test_no_git_outside_repo(tmp_path):
    env = capture(cwd=tmp_path)
    assert "git" not in env


def test_empty_agent_session_preserved(tmp_path):
    """agent="" / session="" explícitos são distintos de "não informado"."""
    env = capture(agent="", session="", cwd=tmp_path)
    assert env["agent"] == ""
    assert env["session"] == ""


def test_capture_survives_deleted_cwd(tmp_path, monkeypatch):
    """Path.cwd() levantando OSError (cwd removido sob o processo) não
    derruba capture() — cai pra um cwd de fallback."""
    with patch("neurata.envelope.Path.cwd", side_effect=OSError("gone")):
        env = capture()
    assert env["cwd"]


def test_capture_survives_gethostname_failure(tmp_path):
    with patch("neurata.envelope.socket.gethostname",
               side_effect=OSError("no host")):
        env = capture(cwd=tmp_path)
    assert env["host"] == "unknown"


def test_git_context_short_output_no_commit_yet(tmp_path):
    """rev-parse com rc=0 mas só 1 linha (repo sem commit em alguns gits)
    ainda produz 'root' sem KeyError/IndexError."""
    fake = subprocess.CompletedProcess(args=[], returncode=0,
                                        stdout=str(tmp_path) + "\n")
    with patch("neurata.envelope.subprocess.run", return_value=fake):
        ctx = _git_context(tmp_path)
    assert ctx == {"root": str(tmp_path.resolve())}
