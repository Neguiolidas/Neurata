"""tests/test_snapshot.py"""
import os
import subprocess

import pytest

from neurata import snapshot
from neurata.home import NeurataHome
from neurata.snapshot import commit, ensure_repo, git_available, has_changes, set_remote


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path_factory, monkeypatch):
    """Nunca deixa config global/system do host vazar para os testes."""
    empty_global = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setattr(snapshot, "_AVAIL", None)


def test_git_available_true():
    assert git_available() is True


def test_ensure_repo_creates_git_once_and_is_idempotent(tmp_path, monkeypatch):
    home = _home(tmp_path)
    assert ensure_repo(home) is True
    assert (home.library / ".git").is_dir()

    calls = []
    orig_run = subprocess.run

    def _spy(*args, **kwargs):
        calls.append(args)
        return orig_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy)
    assert ensure_repo(home) is True
    assert calls == []


def test_ensure_repo_local_identity_isolated(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    name = subprocess.run(
        ["git", "-C", str(home.library), "config", "user.name"],
        capture_output=True, text=True, check=True).stdout.strip()
    gpgsign = subprocess.run(
        ["git", "-C", str(home.library), "config", "commit.gpgsign"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert name == "neurata"
    assert gpgsign == "false"


def test_no_git_everything_returns_falsy_without_raising(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.setattr(snapshot, "_AVAIL", None)
    assert git_available() is False
    assert ensure_repo(home) is False
    assert has_changes(home) is False
    assert commit(home, "subject") is None


def test_commit_skips_when_tree_clean(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    assert commit(home, "no changes yet") is None


def test_commit_returns_sha_prefix(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    sha = commit(home, "feat: primeira nota")
    assert sha is not None
    assert len(sha) >= 12
    assert all(c in "0123456789abcdef" for c in sha)


def test_commit_does_not_inherit_global_identity(tmp_path, tmp_path_factory, monkeypatch):
    global_cfg = tmp_path_factory.mktemp("hostcfg") / "gitconfig"
    global_cfg.write_text("[user]\n\tname = Host User\n\temail = host@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))

    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    commit(home, "feat: identidade isolada")

    author = subprocess.run(
        ["git", "-C", str(home.library), "log", "-1", "--pretty=%an <%ae>"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert author == "neurata <neurata@localhost>"


def test_set_remote_add_then_update(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    set_remote(home, "https://example.invalid/a.git")
    set_remote(home, "https://example.invalid/b.git")
    url = subprocess.run(
        ["git", "-C", str(home.library), "remote", "get-url", "neurata"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert url == "https://example.invalid/b.git"
