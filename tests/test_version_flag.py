"""tests/test_version_flag.py — `neurata --version`.

Precisa responder sem tocar em disco: é o primeiro comando que alguém roda
depois do `pip install`, inclusive para reportar bug com um NEURATA_HOME
quebrado. Por isso os testes apontam para um home inexistente e exigem que
ele continue inexistente no fim.
"""
import json

import pytest

from neurata import __version__
from neurata.cli import main


@pytest.fixture
def home_inexistente(tmp_path, monkeypatch):
    alvo = tmp_path / "nunca-criado"
    monkeypatch.setenv("NEURATA_HOME", str(alvo))
    return alvo


def test_version_texto(home_inexistente, capsys):
    rc = main(["--version"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == f"neurata {__version__}"


def test_version_json(home_inexistente, capsys):
    rc = main(["--json", "--version"])
    assert rc == 0
    env = json.loads(capsys.readouterr().out)
    assert env["ok"] is True
    assert env["command"] == "version"
    assert env["result"]["version"] == __version__


def test_version_nao_cria_home(home_inexistente):
    main(["--version"])
    assert not home_inexistente.exists()


def test_version_ignora_config_quebrada(tmp_path, monkeypatch, capsys):
    """Config inválida derruba os outros comandos; --version tem que passar."""
    home = tmp_path / "h"
    (home / "library").mkdir(parents=True)
    (home / "config.json").write_text("{ isto não é json", encoding="utf-8")
    monkeypatch.setenv("NEURATA_HOME", str(home))

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"neurata {__version__}"


def test_versao_do_pacote_bate_com_pyproject():
    """A versão é dinâmica no pyproject; se a leitura quebrar, o build mente."""
    tomllib = pytest.importorskip("tomllib")
    import pathlib

    raiz = pathlib.Path(__file__).resolve().parent.parent
    cfg = tomllib.loads((raiz / "pyproject.toml").read_text(encoding="utf-8"))
    assert cfg["project"]["dynamic"] == ["version"]
    assert cfg["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "neurata.__version__"
    }
