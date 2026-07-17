"""tests/test_config.py"""
import json

import pytest

from neurata.config import DEFAULTS, ConfigError, load
from neurata.home import NeurataHome


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_defaults_when_missing(tmp_path):
    cfg = load(NeurataHome(tmp_path))  # sem init → sem config.json
    assert cfg == DEFAULTS
    assert cfg is not DEFAULTS


def test_schema_version_key_allowed(tmp_path):
    assert load(_home(tmp_path))["rrf_k"] == 60


def test_override_merges_nested(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps(
        {"schema_version": 1, "rrf_k": 30, "bm25_weights": {"title": 9}}))
    cfg = load(home)
    assert cfg["rrf_k"] == 30
    assert cfg["bm25_weights"]["title"] == 9
    assert cfg["bm25_weights"]["body"] == 1.0
    assert DEFAULTS["bm25_weights"]["title"] == 4.0  # defaults intactos


def test_unknown_key_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"pesso_title": 1}))
    with pytest.raises(ConfigError, match="desconhecida"):
        load(home)


def test_unknown_subkey_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"bm25_weights": {"titulo": 2}}))
    with pytest.raises(ConfigError, match="desconhecida"):
        load(home)


def test_malformed_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text("{nada valido")
    with pytest.raises(ConfigError, match="malformado"):
        load(home)


def test_non_numeric_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"rrf_k": "x"}))
    with pytest.raises(ConfigError, match="num"):
        load(home)
