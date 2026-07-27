"""tests/test_generic_provider.py — walker do provider genérico (Ato 1).

Cobre o que o walker promete pro harvest: ordem determinística, poda de
diretório, e a distinção entre *skip silencioso* (arquivo que nenhum
formato reconhece — ruído esperado num repo) e `Skipped` com motivo
(arquivo que o formato reconheceu mas não deu pra ler).
"""
import os
from pathlib import Path

import pytest

from neurata.providers.generic import (
    FORMATS,
    Scanned,
    WalkConfig,
    accepts,
    oneline,
    scan,
    walk,
)


def _write(base, relpath, text="# Doc\n\nCorpo.\n"):
    path = base / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _names(items):
    return [item.source_path for item in items]


# ------------------------------------------------------------------ walk

def test_walk_is_recursive(tmp_path):
    _write(tmp_path, "a.md")
    _write(tmp_path, "sub/b.md")
    _write(tmp_path, "sub/deep/c.md")
    scanned, skipped = scan(tmp_path)
    assert len(scanned) == 3
    assert skipped == []
    assert all(isinstance(item, Scanned) for item in scanned)


def test_walk_order_is_deterministic(tmp_path):
    for name in ("z.md", "a.md", "m.md"):
        _write(tmp_path, name)
    _write(tmp_path, "sub/b.md")
    first, _ = scan(tmp_path)
    second, _ = scan(tmp_path)
    assert _names(first) == _names(second)
    # Ordem é por diretório (top-down), com os arquivos de cada um ordenados:
    # raiz primeiro, subdiretórios depois — não é lexicográfica global.
    assert _names(first) == [
        str(tmp_path / "a.md"),
        str(tmp_path / "m.md"),
        str(tmp_path / "z.md"),
        str(tmp_path / "sub" / "b.md"),
    ]


def test_walk_prunes_excluded_dirs(tmp_path):
    _write(tmp_path, "keep.md")
    _write(tmp_path, ".git/config.md")
    _write(tmp_path, "node_modules/pkg/README.md")
    _write(tmp_path, "__pycache__/x.md")
    scanned, skipped = scan(tmp_path)
    assert _names(scanned) == [str(tmp_path / "keep.md")]
    assert skipped == []


def test_walk_missing_dir_returns_empty(tmp_path):
    scanned, skipped = scan(tmp_path / "nao-existe")
    assert scanned == []
    assert skipped == []


def test_walk_ignores_unknown_extensions_silently(tmp_path):
    """`.png`/`.go` não viram Skipped: ruído esperado, não falha (NFR3)."""
    _write(tmp_path, "doc.md")
    _write(tmp_path, "foto.png", "não é markdown")
    _write(tmp_path, "main.go", "package main")
    scanned, skipped = scan(tmp_path)
    assert _names(scanned) == [str(tmp_path / "doc.md")]
    assert skipped == []


# --------------------------------------------------------------- skipped

def test_walk_skips_file_over_max_size(tmp_path):
    path = _write(tmp_path, "gordo.md", "x" * 200)
    scanned, skipped = scan(tmp_path, max_size=100)
    assert scanned == []
    assert len(skipped) == 1
    assert skipped[0].path == str(path)
    assert "too large" in skipped[0].reason


def test_walk_skips_binary_file(tmp_path):
    path = tmp_path / "bin.md"
    path.write_bytes(b"# Doc\n\x00\x01binario")
    scanned, skipped = scan(tmp_path)
    assert scanned == []
    assert len(skipped) == 1
    assert "binary" in skipped[0].reason


def test_walk_skips_non_utf8_file(tmp_path):
    path = tmp_path / "latin.md"
    path.write_bytes("# Título com acento\n".encode("latin-1"))
    scanned, skipped = scan(tmp_path)
    assert scanned == []
    assert len(skipped) == 1
    assert "not utf-8" in skipped[0].reason


def test_walk_skips_empty_file(tmp_path):
    _write(tmp_path, "vazio.md", "   \n\n")
    scanned, skipped = scan(tmp_path)
    assert scanned == []
    assert len(skipped) == 1
    assert skipped[0].reason == "empty"


def test_walk_skips_unreadable_file(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignora permissões de arquivo")
    path = _write(tmp_path, "secreto.md")
    os.chmod(path, 0o000)
    try:
        scanned, skipped = scan(tmp_path)
    finally:
        os.chmod(path, 0o644)
    assert scanned == []
    assert len(skipped) == 1
    assert "unreadable" in skipped[0].reason


def test_walk_adapter_exception_does_not_abort_batch(tmp_path, monkeypatch):
    """NFR3: adapter torto derruba UM arquivo, não o batch de 13k."""
    from neurata.providers.formats import markdown

    def explode(path, text):
        if path.name == "bomba.md":
            raise RuntimeError("adapter torto")
        return Scanned("ok", "", text, str(path), "markdown")

    monkeypatch.setattr(markdown, "parse", explode)
    _write(tmp_path, "antes.md")
    _write(tmp_path, "bomba.md")
    _write(tmp_path, "depois.md")
    scanned, skipped = scan(tmp_path)
    assert len(scanned) == 2
    assert len(skipped) == 1
    assert "adapter torto" in skipped[0].reason


# ----------------------------------------------------------- formato fixo

def test_fixed_format_reads_skill_md_as_markdown(tmp_path):
    """`--format markdown` numa árvore de skills colhe os SKILL.md."""
    _write(tmp_path, "skills/foo/SKILL.md",
           "---\nname: foo\n---\n# Foo\n\nCorpo.\n")
    scanned, _ = scan(tmp_path, fmt="markdown")
    assert len(scanned) == 1
    assert scanned[0].fmt == "markdown"


def test_fixed_format_ignores_files_of_other_extensions(tmp_path):
    """Formato fixo não trata `.png` como markdown."""
    _write(tmp_path, "doc.md")
    _write(tmp_path, "foto.png", "binário-ish")
    _write(tmp_path, "conf.yaml", "name: x\n")
    scanned, skipped = scan(tmp_path, fmt="markdown")
    assert _names(scanned) == [str(tmp_path / "doc.md")]
    assert skipped == []


def test_fixed_format_yaml_only_takes_yaml(tmp_path):
    _write(tmp_path, "a.yaml", "name: A\n")
    _write(tmp_path, "b.yml", "name: B\n")
    _write(tmp_path, "c.md")
    scanned, _ = scan(tmp_path, fmt="yaml")
    assert sorted(item.name for item in scanned) == ["A", "B"]


def test_unknown_format_raises(tmp_path):
    with pytest.raises(ValueError, match="formato desconhecido"):
        scan(tmp_path, fmt="parquet")


def test_formats_tuple_starts_with_auto():
    assert FORMATS[0] == "auto"
    assert set(FORMATS) >= {"auto", "skill-md", "markdown", "yaml", "rules"}


# -------------------------------------------------------------- progresso

def test_on_progress_called_once_per_candidate(tmp_path):
    _write(tmp_path, "a.md")
    _write(tmp_path, "b.md")
    _write(tmp_path, "foto.png", "ignorado")
    seen = []
    scan(tmp_path, on_progress=lambda n, path: seen.append((n, path.name)))
    assert seen == [(1, "a.md"), (2, "b.md")]


def test_on_progress_optional(tmp_path):
    _write(tmp_path, "a.md")
    scanned, _ = walk(WalkConfig(root=tmp_path))
    assert len(scanned) == 1


# ---------------------------------------------------------------- oneline

def test_oneline_collapses_whitespace():
    assert oneline("a\nb   c\t d") == "a b c d"


def test_oneline_truncates_to_limit():
    assert len(oneline("palavra " * 100)) <= 200


def test_oneline_empty_stays_empty():
    assert oneline("   \n  ") == ""


def test_walk_valida_formato_antes_da_raiz(tmp_path):
    """cfg inválido falha sempre, não só quando a raiz existe."""
    with pytest.raises(ValueError, match="formato desconhecido"):
        walk(WalkConfig(root=tmp_path / "nao-existe", fmt="inventado"))


def test_accepts_rejeita_fmt_nao_resolvido():
    with pytest.raises(ValueError, match="sufixos"):
        accepts("auto", Path("x.md"))


# --- exclude_roots -------------------------------------------------
# A poda que impede o harvest de re-ingerir o próprio NEURATA_HOME
# quando ele mora dentro do diretório colhido.

def test_walk_poda_subarvore_em_exclude_roots(tmp_path):
    (tmp_path / "fora.md").write_text("# Fora\n", encoding="utf-8")
    home = tmp_path / "home"
    (home / "library").mkdir(parents=True)
    (home / "library" / "dentro.md").write_text("# Dentro\n", encoding="utf-8")

    scanned, _ = walk(WalkConfig(root=tmp_path, exclude_roots=(home,)))

    assert [Path(s.source_path).name for s in scanned] == ["fora.md"]


def test_walk_vazio_quando_a_raiz_esta_bloqueada(tmp_path):
    (tmp_path / "doc.md").write_text("# Doc\n", encoding="utf-8")

    scanned, skipped = walk(WalkConfig(root=tmp_path, exclude_roots=(tmp_path,)))

    assert scanned == [] and skipped == []


def test_walk_poda_symlink_que_aponta_para_root_bloqueada(tmp_path):
    home = tmp_path / "home"
    (home / "library").mkdir(parents=True)
    (home / "library" / "dentro.md").write_text("# Dentro\n", encoding="utf-8")
    (tmp_path / "atalho").symlink_to(home / "library")

    scanned, _ = walk(WalkConfig(root=tmp_path, exclude_roots=(home,)))

    assert scanned == []


def test_walk_sem_exclude_roots_nao_poda_nada(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "doc.md").write_text("# Doc\n", encoding="utf-8")

    scanned, _ = walk(WalkConfig(root=tmp_path))

    assert [Path(s.source_path).name for s in scanned] == ["doc.md"]
