"""tests/test_home.py"""
import json

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
    assert CONTRACT_VERSION == 1


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
