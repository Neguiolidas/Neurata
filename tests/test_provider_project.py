"""tests/test_provider_project.py — colheita das instruções do projeto (v1.7).

O conjunto canônico do repo, a raiz como âncora ("onde estou", mesma do
contexto da busca), namespace por hash da raiz, chave por caminho
relativo. Ausente é silêncio; presente-e-ilegível é skipped.
"""
import hashlib

from neurata.providers import project
from neurata.providers.project import default_dir, namespace, scan


def _repo(tmp_path, *, arquivos=None, mdc=None):
    """Repo fake com o conjunto canônico; devolve a raiz."""
    arquivos = arquivos if arquivos is not None else {
        "AGENTS.md": "# Agentes\n\nComo trabalhar neste repo.\n",
        "CLAUDE.md": "Instruções para o Claude.\n",
        ".github/copilot-instructions.md": "Copilot: siga o padrão.\n",
        ".cursorrules": "Regra legada do Cursor.\n",
    }
    for rel, corpo in arquivos.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(corpo, encoding="utf-8")
    for nome, corpo in (mdc or {}).items():
        p = tmp_path / ".cursor" / "rules" / nome
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(corpo, encoding="utf-8")
    return tmp_path


def test_conjunto_canonico_com_fmt_por_forma(tmp_path):
    repo = _repo(tmp_path)
    skills, skipped = scan(repo)
    por_key = {s.key: s for s in skills}
    assert set(por_key) == {"AGENTS.md", "CLAUDE.md",
                            ".github/copilot-instructions.md",
                            ".cursorrules"}
    assert por_key["AGENTS.md"].fmt == "markdown"
    assert por_key["CLAUDE.md"].fmt == "markdown"
    assert por_key[".github/copilot-instructions.md"].fmt == "markdown"
    assert por_key[".cursorrules"].fmt == "rules"
    assert skipped == []
    # corpo veio do arquivo, não de invenção
    assert "Como trabalhar neste repo." in por_key["AGENTS.md"].body


def test_cursor_rules_mdc_glob_ordenado(tmp_path):
    repo = _repo(tmp_path, arquivos={}, mdc={
        "zeta.mdc": "---\ndescription: Zeta\n---\nCorpo zeta.\n",
        "alpha.mdc": "---\ndescription: Alpha\n---\nCorpo alpha.\n",
    })
    skills, skipped = scan(repo)
    assert skipped == []
    assert [s.key for s in skills] == [".cursor/rules/alpha.mdc",
                                       ".cursor/rules/zeta.mdc"]
    assert all(s.fmt == "mdc" for s in skills)
    assert "Alpha" in skills[0].description


def test_arquivo_ausente_e_silencio(tmp_path):
    raiz = tmp_path / "raiz"
    raiz.mkdir()
    skills, skipped = scan(raiz)
    assert skills == [] and skipped == []


def test_raiz_ausente_retorna_vazio(tmp_path):
    skills, skipped = scan(tmp_path / "nao-existe")
    assert skills == [] and skipped == []
    assert scan(None) == ([], [])


def test_legivel_e_skipped(tmp_path, monkeypatch):
    repo = _repo(tmp_path, arquivos={"AGENTS.md": "ok\n"})
    from neurata.providers import generic
    monkeypatch.setattr(generic, "_read_text",
                        lambda path, max_size: (None, "binary: null byte"))
    skills, skipped = scan(repo)
    assert skills == []
    assert len(skipped) == 1
    assert "AGENTS.md" in skipped[0].path
    assert skipped[0].reason == "binary: null byte"


def test_default_dir_env_vence_git(tmp_path, monkeypatch):
    fake = tmp_path / "override"
    fake.mkdir()
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(fake))
    assert default_dir() == fake


def test_default_dir_git_root_do_cwd(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("neurata.context.git_root", lambda cwd=None: tmp_path)
    assert default_dir() == tmp_path


def test_default_dir_sem_raiz_e_none(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT_ROOT", raising=False)
    monkeypatch.setattr("neurata.context.git_root", lambda cwd=None: None)
    assert default_dir() is None


def test_namespace_estavel_e_distinto_por_raiz(tmp_path):
    a = tmp_path / "repo-a"
    b = tmp_path / "repo-b"
    a.mkdir()
    b.mkdir()
    na, nb = namespace(a), namespace(b)
    assert na == namespace(a)          # estável
    assert na != nb                    # raiz distinta, namespace distinto
    assert na.startswith("project@") and len(na) == len("project@") + 12
    digest = hashlib.sha256(a.resolve().as_posix().encode()).hexdigest()[:12]
    assert na == f"project@{digest}"   # mesma fórmula do genérico


def test_namespace_sem_raiz(tmp_path):
    assert namespace(None) == "project@sem-raiz" or namespace(None).startswith(
        "project@")


def test_item_key_e_o_relpath(tmp_path):
    repo = _repo(tmp_path, arquivos={"AGENTS.md": "x\n"})
    skills, _ = scan(repo)
    assert project.item_key(skills[0]) == "AGENTS.md"
