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


def test_shelf_non_numeric_still_fails(tmp_path):
    """Regressão: subtree numérico (shelf) não deve ser desviado pro
    ramo tipado de snapshot — continua validando via _require_number."""
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"shelf": {"tau_dias": "x"}}))
    with pytest.raises(ConfigError, match="num"):
        load(home)


def test_snapshot_default_when_missing(tmp_path):
    cfg = load(_home(tmp_path))
    assert cfg["snapshot"] == {"remote": None, "auto_push": False}


def test_snapshot_valid_loads(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps(
        {"snapshot": {"remote": "origin", "auto_push": True}}))
    cfg = load(home)
    assert cfg["snapshot"] == {"remote": "origin", "auto_push": True}


def test_snapshot_auto_push_non_bool_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"snapshot": {"auto_push": 1}}))
    with pytest.raises(ConfigError, match="snapshot"):
        load(home)


def test_snapshot_auto_push_float_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"snapshot": {"auto_push": 1.0}}))
    with pytest.raises(ConfigError, match="snapshot"):
        load(home)


def test_snapshot_remote_non_str_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"snapshot": {"remote": 5}}))
    with pytest.raises(ConfigError, match="snapshot"):
        load(home)


def test_snapshot_unknown_subkey_fails(tmp_path):
    home = _home(tmp_path)
    home.config_path.write_text(json.dumps({"snapshot": {"branch": "main"}}))
    with pytest.raises(ConfigError, match="desconhecida"):
        load(home)


def test_regime_quota_default_e_validada(tmp_path):
    home = _home(tmp_path)
    assert load(home)["regime"]["curated_quota"] == 3
    home.config_path.write_text(
        json.dumps({"regime": {"curated_quota": "tres"}}))
    with pytest.raises(ConfigError, match="regime"):
        load(home)
    home.config_path.write_text(json.dumps({"regime": {"cota": 3}}))
    with pytest.raises(ConfigError, match="desconhecida"):
        load(home)
