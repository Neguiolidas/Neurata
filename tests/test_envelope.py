"""tests/test_envelope.py"""
import subprocess

from armarium.envelope import capture


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
