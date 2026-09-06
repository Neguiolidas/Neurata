"""tests/test_format_mdc.py — adapter de regras do Cursor (v1.7).

Um .mdc é a regra moderna do Cursor: markdown com frontmatter
(description, globs, alwaysApply). O adapter é o ponto que declara que
aquilo é procedimento por estrutura — classe `procedural` pela forma,
mesma régua do `rules`.
"""
from pathlib import Path

from neurata.providers.formats import FORMAT_CLASS
from neurata.providers.formats.mdc import parse
from neurata.providers.generic import accepts, resolve_format


def test_frontmatter_completo():
    text = ("---\ndescription: Padrao de codigo Python\nglobs: "
            '"**/*.py"\nalwaysApply: false\n---\nUse type hints '
            "em toda funcao.\n")
    s = parse(Path("rules/python-style.mdc"), text)
    assert s is not None
    assert s.name == "python-style"
    assert "Padrao de codigo Python" in s.description
    assert s.body.startswith("Use type hints")
    assert s.fmt == "mdc"
    assert s.source_path.endswith("python-style.mdc")


def test_globs_vira_sinal_na_descricao():
    text = ("---\ndescription: Regra de testes\nglobs: [tests/**, "
            "*_test.py]\n---\nCorpo da regra.\n")
    s = parse(Path("r/tests.mdc"), text)
    assert "globs: tests/**, *_test.py" in s.description


def test_globs_sem_description():
    text = "---\nglobs: \"**/*.sql\"\n---\nCorpo.\n"
    s = parse(Path("r/sql.mdc"), text)
    assert s.description.startswith("globs:")


def test_sem_frontmatter_fallback_texto_inteiro():
    text = "# Regra solta\n\nConteudo sem frontmatter.\n"
    s = parse(Path("r/solta.mdc"), text)
    assert s is not None
    assert s.name == "solta"
    assert s.body == text


def test_frontmatter_torto_fallback():
    text = "---\ndescription sem dois pontos\n---\nCorpo valido.\n"
    s = parse(Path("r/torto.mdc"), text)
    assert s is not None
    assert "Corpo valido." in s.body


def test_corpo_vazio_e_none():
    assert parse(Path("r/vazio.mdc"), "---\ndescription: x\n---\n  \n") \
        is None


def test_crlf_preserva_meta():
    text = "---\r\ndescription: Regra CRLF\r\n---\r\nCorpo.\r\n"
    s = parse(Path("r/crlf.mdc"), text)
    assert "Regra CRLF" in s.description
    assert s.body == "Corpo.\n"


def test_classe_procedural_pela_forma():
    assert FORMAT_CLASS["mdc"] == "procedural"


def test_walk_generico_enxerga_mdc():
    assert resolve_format(Path("repo/.cursor/rules/x.mdc")) == "mdc"
    assert accepts("mdc", Path("repo/.cursor/rules/x.mdc"))


def test_stem_vazio_cai_no_pai():
    # nome de arquivo sem stem é improvável; o fallback não pode estourar
    s = parse(Path("rules"), "---\ndescription: d\n---\ncorpo\n")
    assert s is not None and s.name
