"""tests/test_home.py"""
import json

import pytest

from neurata.home import CONTRACT_VERSION, SCHEMA_VERSION, NeurataHome


def test_env_var_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path / "h"))
    home = NeurataHome()
    assert home.root == tmp_path / "h"


def test_init_creates_layout(tmp_path):
    home = NeurataHome(tmp_path / "a")
    home.init()
    for d in (home.library, home.inbox, home.archive, home.quarantine, home.logs):
        assert d.is_dir()
    cfg = home.load_config()
    assert cfg["schema_version"] == SCHEMA_VERSION
    assert CONTRACT_VERSION == 3


def test_init_idempotent_preserves_config(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    cfg = json.loads(home.config_path.read_text())
    cfg["custom"] = "keep"
    home.config_path.write_text(json.dumps(cfg))
    home.init()
    assert home.load_config()["custom"] == "keep"


def test_append_and_read_log(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    home.append_log("deposits", {"hash": "abc"})
    home.append_log("deposits", {"hash": "def"})
    (home.logs / "deposits.jsonl").open("a").write("LINHA CORROMPIDA\n")
    records = home.read_log("deposits")
    assert [r["hash"] for r in records] == ["abc", "def"]


@pytest.mark.parametrize("bad_name", [
    "../escape", "/etc/passwd", "a/b", "a\\b", "", "with space", "a.b",
])
def test_append_log_rejects_unsafe_name(tmp_path, bad_name):
    home = NeurataHome(tmp_path)
    home.init()
    with pytest.raises(ValueError):
        home.append_log(bad_name, {"x": 1})


@pytest.mark.parametrize("bad_name", [
    "../escape", "/etc/passwd", "a/b", "a\\b", "", "with space", "a.b",
])
def test_read_log_rejects_unsafe_name(tmp_path, bad_name):
    home = NeurataHome(tmp_path)
    home.init()
    with pytest.raises(ValueError):
        home.read_log(bad_name)


def test_env_var_falsy_root_not_silently_overridden(tmp_path, monkeypatch):
    """root="" explícito não deve cair pro env var (root is not None)."""
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path / "from-env"))
    home = NeurataHome("")
    assert home.root != tmp_path / "from-env"
