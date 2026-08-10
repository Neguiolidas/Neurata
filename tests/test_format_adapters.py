"""tests/test_format_adapters.py — adapters de formato do provider genérico.

Cada adapter recebe `(path, text)` já lido pelo walker e devolve `Scanned`
ou `None`. O que estes testes protegem: os fallbacks (arquivo sem título
não pode sumir do índice) e o invariante de que `name`/`description`
nunca contêm `\\n` — frontmatter multilinha quebra o `parse` do tick.
"""
from pathlib import Path

import pytest

from neurata.providers.formats import markdown, rules, skill_md
from neurata.providers.formats import yaml as yaml_fmt
from neurata.providers.generic import resolve_format

SKILL_MD = """---
name: foo-skill
description: faz coisas de foo
---
# Foo

Do foo things.
"""


def _skill(tmp_path: Path, text: str, dirname: str = "foo") -> Path:
    d = tmp_path / dirname
    d.mkdir()
    path = d / "SKILL.md"
    path.write_text(text, encoding="utf-8")
    return path


# --------------------------------------------------------------- skill-md

def test_skill_md_uses_frontmatter(tmp_path):
    path = _skill(tmp_path, SKILL_MD)
    item = skill_md.parse(path, SKILL_MD)
    assert item.name == "foo-skill"
    assert item.description == "faz coisas de foo"
    assert item.body == "# Foo\n\nDo foo things.\n"
    assert item.fmt == "skill-md"
    assert item.source_path == str(path)


def test_skill_md_without_frontmatter_falls_back_to_dirname(tmp_path):
    text = "# Guia\n\nSem frontmatter aqui.\n"
    path = _skill(tmp_path, text, dirname="meu-guia")
    item = skill_md.parse(path, text)
    assert item.name == "meu-guia"
    assert item.description == ""
    assert item.body == text


def test_skill_md_broken_frontmatter_is_not_discarded(tmp_path):
    """R3: frontmatter sem terminador vira corpo inteiro, não erro."""
    text = "---\nname: sem terminador\n"
    path = _skill(tmp_path, text, dirname="quebrado")
    item = skill_md.parse(path, text)
    assert item.name == "quebrado"
    assert item.body == text


def test_skill_md_only_frontmatter_returns_none(tmp_path):
    text = "---\nname: vazio\ndescription: nada\n---\n\n"
    path = _skill(tmp_path, text, dirname="vazio")
    assert skill_md.parse(path, text) is None


def test_skill_md_multiline_description_is_collapsed(tmp_path):
    """Invariante: description de uma linha só, senão o tick não relê."""
    text = "---\nname: x\ndescription: 'linha um linha dois'\n---\ncorpo\n"
    path = _skill(tmp_path, text)
    item = skill_md.parse(path, text)
    assert "\n" not in item.description


# --------------------------------------------------------------- markdown

def test_markdown_uses_h1_as_title(tmp_path):
    text = "# Título Real\n\nCorpo do doc.\n"
    path = tmp_path / "doc.md"
    item = markdown.parse(path, text)
    assert item.name == "Título Real"
    assert item.description == "Corpo do doc."
    assert item.body == text
    assert item.fmt == "markdown"


def test_markdown_without_h1_falls_back_to_filename(tmp_path):
    text = "Só prosa, nenhum cabeçalho.\n"
    item = markdown.parse(tmp_path / "notas-soltas.md", text)
    assert item.name == "notas-soltas"
    assert item.description == "Só prosa, nenhum cabeçalho."


def test_markdown_ignores_h2_as_title(tmp_path):
    text = "## Subtítulo\n\nCorpo.\n"
    item = markdown.parse(tmp_path / "sub.md", text)
    assert item.name == "sub"


def test_markdown_h1_after_badges_is_found(tmp_path):
    text = "<img src=badge>\n\n# Projeto X\n\nDescrição.\n"
    item = markdown.parse(tmp_path / "README.md", text)
    assert item.name == "Projeto X"
    assert item.description == "Descrição."


def test_markdown_body_keeps_h1(tmp_path):
    text = "# Título\n\nCorpo.\n"
    item = markdown.parse(tmp_path / "d.md", text)
    assert item.body == text


# ------------------------------------------------------------------- yaml

def test_yaml_name_key_wins(tmp_path):
    text = "id: agent-001\nname: Agente Foo\ndescription: faz foo\n"
    item = yaml_fmt.parse(tmp_path / "a.yaml", text)
    assert item.name == "Agente Foo"
    assert item.description == "faz foo"
    assert item.fmt == "yaml"


def test_yaml_id_used_when_no_name(tmp_path):
    text = "id: agent-001\nversion: 2\n"
    item = yaml_fmt.parse(tmp_path / "a.yml", text)
    assert item.name == "agent-001"


def test_yaml_openapi_info_name(tmp_path):
    text = "openapi: 3.0.0\ninfo:\n  name: API de Foo\n  description: rotas\n"
    item = yaml_fmt.parse(tmp_path / "api.yaml", text)
    assert item.name == "API de Foo"
    assert item.description == "rotas"


@pytest.mark.parametrize("text,name,desc", [
    ("id: agent-001\nname: Agente Foo\ndescription: faz foo\n",
     "Agente Foo", "faz foo"),
    ("id: agent-001\nversion: 2\n", "agent-001", None),
    ("openapi: 3.0.0\ninfo:\n  name: API de Foo\n  description: rotas\n",
     "API de Foo", "rotas"),
])
def test_yaml_titulo_independe_de_pyyaml(tmp_path, monkeypatch, text, name,
                                         desc):
    """Instalar/desinstalar PyYAML não pode mudar título nem descrição.

    Se mudar, o mesmo arquivo entra duas vezes no índice. PyYAML é dep
    opcional: sem este teste a suíte só exercita o caminho de quem o tem
    instalado — foi assim que `info.name` ficou sem fallback no regex.
    """
    com_yaml = yaml_fmt.parse(tmp_path / "a.yaml", text)
    monkeypatch.setattr(yaml_fmt, "_yaml", None)
    sem_yaml = yaml_fmt.parse(tmp_path / "a.yaml", text)

    assert com_yaml.name == sem_yaml.name == name
    if desc is not None:
        assert com_yaml.description == sem_yaml.description == desc


def test_yaml_info_ignora_neto(tmp_path, monkeypatch):
    """`info.contact.name` é neto: não é título, com ou sem PyYAML."""
    text = "openapi: 3.0.0\ninfo:\n  contact:\n    name: Zé\n"
    monkeypatch.setattr(yaml_fmt, "_yaml", None)
    item = yaml_fmt.parse(tmp_path / "api.yaml", text)
    assert item.name == "api"


def test_yaml_invalid_falls_back_to_regex(tmp_path):
    """YAML torto ainda tem título se a linha `name:` existir."""
    text = "name: Meio Torto\n\t- lista: [inválida\n"
    item = yaml_fmt.parse(tmp_path / "torto.yaml", text)
    assert item.name == "Meio Torto"


def test_yaml_unparseable_and_nameless_uses_filename(tmp_path):
    text = "- a\n- b\n"
    item = yaml_fmt.parse(tmp_path / "lista.yaml", text)
    assert item.name == "lista"


def test_yaml_without_pyyaml_matches_pyyaml_title(tmp_path, monkeypatch):
    """Instalar/desinstalar PyYAML não pode mudar o título (dedup)."""
    text = "id: agent-001\nname: Agente Foo\ndescription: faz foo\n"
    path = tmp_path / "a.yaml"
    with_yaml = yaml_fmt.parse(path, text)
    monkeypatch.setattr(yaml_fmt, "_yaml", None)
    without_yaml = yaml_fmt.parse(path, text)
    assert without_yaml.name == with_yaml.name == "Agente Foo"
    assert without_yaml.description == with_yaml.description == "faz foo"


def test_yaml_indented_name_is_not_top_level_title(tmp_path, monkeypatch):
    """No fallback regex, `  name:` aninhado não pode virar título."""
    monkeypatch.setattr(yaml_fmt, "_yaml", None)
    text = "steps:\n  name: passo interno\n"
    item = yaml_fmt.parse(tmp_path / "wf.yaml", text)
    assert item.name == "wf"


# ------------------------------------------------------------------ rules

def test_rules_uses_dirname_and_filename(tmp_path):
    d = tmp_path / "meu-projeto"
    d.mkdir()
    text = "Sempre responda em português.\nUse tabs.\n"
    item = rules.parse(d / ".cursorrules", text)
    assert item.name == "meu-projeto (.cursorrules)"
    assert item.description == "Sempre responda em português. Use tabs."
    assert item.body == text
    assert item.fmt == "rules"


def test_rules_description_is_truncated_and_single_line(tmp_path):
    text = "\n".join(f"regra {i}" for i in range(200))
    item = rules.parse(tmp_path / ".windsurfrules", text)
    assert len(item.description) <= 200
    assert "\n" not in item.description


# ----------------------------------------------------------- auto-detect

@pytest.mark.parametrize("name,expected", [
    ("SKILL.md", "skill-md"),
    ("guia.md", "markdown"),
    ("guia.markdown", "markdown"),
    ("agent.yaml", "yaml"),
    ("agent.yml", "yaml"),
    (".cursorrules", "rules"),
    (".windsurfrules", "rules"),
    (".clinerules", "rules"),
    ("foto.png", None),
    ("main.go", None),
])
def test_resolve_format(name, expected):
    assert resolve_format(Path("/tmp/x") / name) == expected


def test_resolve_format_skill_md_beats_markdown():
    """Convenção mais específica ganha: SKILL.md não é markdown genérico."""
    assert resolve_format(Path("/a/b/SKILL.md")) == "skill-md"
    assert resolve_format(Path("/a/b/skill.md")) == "markdown"
