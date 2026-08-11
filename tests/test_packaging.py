"""tests/test_packaging.py

O empacotamento é o único artefato que a suíte não exercita por acidente:
CI instala com `pip install -e`, que joga a árvore inteira no `sys.path`.
Um subpacote ausente da declaração some do wheel sem que um único teste
fique vermelho — foi assim que `neurata.providers` ficou de fora e todo
comando publicado morreria em `ModuleNotFoundError`.

Este teste compara a declaração do `pyproject.toml` com o disco. É rápido
e falha localmente; o gate de verdade (instalar o wheel construído num
venv limpo) mora na CI, porque exige build.
"""
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _packages_on_disk() -> set[str]:
    """Todo diretório com __init__.py sob neurata/, em notação de import."""
    return {
        ".".join(p.parent.relative_to(ROOT).parts)
        for p in (ROOT / "neurata").rglob("__init__.py")
    }


def _packages_declared() -> set[str]:
    """O que o build realmente vai empacotar, lido do pyproject."""
    tomllib = pytest.importorskip(
        "tomllib", reason="stdlib só a partir do 3.11; a config não varia por versão"
    )
    setuptools = pytest.importorskip("setuptools")

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    tool = cfg["tool"]["setuptools"]

    # Lista fixa (a forma que falhou) — comparada como está.
    if "packages" in tool and isinstance(tool["packages"], list):
        return set(tool["packages"])

    find = tool["packages"]["find"]
    return set(
        setuptools.find_packages(
            where=str(ROOT),
            include=find.get("include", ["*"]),
            exclude=find.get("exclude", []),
        )
    )


def test_todo_subpacote_do_disco_esta_declarado():
    faltando = _packages_on_disk() - _packages_declared()
    assert not faltando, (
        f"subpacote(s) fora do wheel: {sorted(faltando)} — "
        "todo comando quebra em ModuleNotFoundError no pip install"
    )


def test_declaracao_nao_promete_pacote_inexistente():
    fantasma = _packages_declared() - _packages_on_disk()
    assert not fantasma, f"declarado mas não existe no disco: {sorted(fantasma)}"


def test_disco_tem_os_subpacotes_conhecidos():
    """Âncora: se estes somem, foi refactor, não engano de empacotamento."""
    assert {"neurata", "neurata.providers", "neurata.providers.formats"} <= (
        _packages_on_disk()
    )
