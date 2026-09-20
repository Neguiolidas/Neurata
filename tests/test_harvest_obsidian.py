"""tests/test_harvest_obsidian.py — `neurata harvest obsidian-vault` (v1.13).

Convenções de vault: tags inline `#tag`, pasta imediata como tag,
exclusões `.obsidian/`/`.trash/`, aliases até `entry_aliases`, embed
`![[x.png]]` que não é link, e o contrato `exclude_roots` para providers
root-scoped. Vault sintético → harvest → tick → índice → busca.

O import do provider é LAZY (helper `_vault`): no vermelho, cada teste
falha pela razão dele — `ModuleNotFoundError` nos do provider,
contagem errada nos de embed, `TypeError` no de `exclude_roots` — em
vez de um erro de coleção que não diz nada.
"""
import json
from pathlib import Path

import pytest

import neurata.frontmatter as fm
from neurata.harvest import harvest
from neurata.home import NeurataHome
from neurata.reindex import reindex


def _vault() -> "object":
    from neurata.providers import obsidian_vault
    return obsidian_vault


def _home(path) -> NeurataHome:
    home = NeurataHome(Path(path))
    home.init()
    return home


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _nota(nome: str, frontmatter: str = "", corpo: str = "Prosa.\n") -> str:
    fmtext = f"---\n{frontmatter}---\n" if frontmatter else ""
    return f"{fmtext}{corpo}"


def _espelhos(home) -> dict:
    """source_key → (meta, body) dos itens no inbox."""
    out = {}
    for p in sorted(home.inbox.glob("*.md")):
        meta, body = fm.parse(p.read_text(encoding="utf-8"))
        sk = meta.get("source_key")
        if sk:
            out[str(sk)] = (meta, body)
    return out


VAULT = "obsidian-vault"


def _set_vault(monkeypatch, path) -> None:
    monkeypatch.setenv("NEURATA_OBSIDIAN_VAULT", str(path))


# ── provider: colheita das convenções ───────────────────────────────

def test_vault_end_to_end(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "meu-vault"
    _write(vault / "Area" / "Nota.md",
           _nota("Nota", "tags: [projeto]\naliases: [N]\n",
                 "Corpo com #proto e [[Alvo]] dentro.\n"))
    _write(vault / "Alvo.md", _nota("Alvo", corpo="Alvo da aresta.\n"))
    _set_vault(monkeypatch, vault)

    rep = harvest(home, VAULT)
    assert rep.harvested == 2, "duas notas, nenhum skip"
    assert rep.skipped == []

    espelhos = _espelhos(home)
    # localiza pela chave que termina em area/nota.md (namespace tem hash)
    nota = next(v for k, v in espelhos.items() if k.endswith(":Area/Nota.md"))
    meta, _ = nota
    # frontmatter + inline + pasta imediata, lower, dedup
    assert meta["tags"] == ["area", "projeto", "proto"]
    assert meta["aliases"] == ["N"]
    assert meta["env"] == VAULT
    assert meta["class"] == "semantic"       # fmt markdown fixo
    assert meta["source_key"].startswith(f"{VAULT}@")
    assert str(meta["id"]).startswith("01")  # ULID


def test_vault_skill_md_e_prosa(tmp_path, monkeypatch):
    """SKILL.md de um vault é documento do vault, não skill."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "SKILL.md", _nota("SKILL", corpo="Prosa de vault.\n"))
    _set_vault(monkeypatch, vault)
    rep = harvest(home, VAULT)
    assert rep.harvested == 1
    (meta, _), = _espelhos(home).values()
    assert meta["class"] == "semantic"


def test_vault_inline_tag_rules(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "Regras.md", _nota("Regras", corpo=(
        "#y1984 é tag; #1984 não é.\n"
        "# Título com espaço não é tag\n"
        "#colada é tag\n"
        "grudada#meio não é\n"
        "#pai/filho é uma tag nested\n"
        "```python\n#codigo não é tag\n```\n"
        "~~~\n#cerca tb não\n~~~\n"
        "#área acentuada é\n")))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    (meta, _), = _espelhos(home).values()
    assert meta["tags"] == ["colada", "pai/filho", "y1984", "área"]


def test_vault_fence_nao_fechada(tmp_path, monkeypatch):
    """Fence aberto até EOF: o resto do arquivo é código, não tags."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "X.md", _nota("X", corpo="```\n#tudo codigo\n#sem fechamento\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    (meta, _), = _espelhos(home).values()
    # tags vazias = chave ausente (emissor da v1.8 não grava lista vazia)
    assert not meta.get("tags")


def test_vault_exclusions(tmp_path, monkeypatch):
    """`.obsidian/` (config+plugins) e `.trash/` (deletadas) não colhem."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "Nota.md", _nota("Nota"))
    _write(vault / ".obsidian" / "plugins" / "x" / "README.md",
           _nota("Plugin Readme"))
    _write(vault / ".trash" / "apagada.md", _nota("Apagada"))
    _write(vault / "sub" / ".obsidian" / "app.md", _nota("App cfg"))
    _set_vault(monkeypatch, vault)
    rep = harvest(home, VAULT)
    assert rep.harvested == 1
    chaves = " ".join(_espelhos(home))
    assert "plugin readme" not in chaves.lower()
    assert "apagada" not in chaves.lower()


def test_vault_sem_env_reporta_skip(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path / "home")
    monkeypatch.delenv("NEURATA_OBSIDIAN_VAULT", raising=False)
    rep = harvest(home, VAULT)
    assert rep.harvested == 0 and rep.updated == 0 and rep.removed == 0
    assert len(rep.skipped) == 1
    assert "NEURATA_OBSIDIAN_VAULT" in rep.skipped[0].reason


def test_vault_reharvest_noop(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "A.md", _nota("A", corpo="texto #um\n"))
    _set_vault(monkeypatch, vault)
    first = harvest(home, VAULT)
    assert first.harvested == 1
    second = harvest(home, VAULT)
    assert (second.harvested, second.updated, second.removed) == (0, 0, 0)


def test_vault_tag_inline_muda_reemite(tmp_path, monkeypatch):
    """Corpo com nova tag inline = emissão diferente = update, não skip."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    p = _write(vault / "A.md", _nota("A", corpo="texto\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    p.write_text(_nota("A", corpo="texto #novo\n"), encoding="utf-8")
    from neurata.tick import curate_tick
    curate_tick(home)  # índice conhece o item: mudança é update, não novo
    rep = harvest(home, VAULT)
    assert rep.updated == 1 and rep.harvested == 0


def test_vault_move_entre_pastas(tmp_path, monkeypatch):
    """Mover = source_key novo: item novo + tombstone do antigo."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    origem = _write(vault / "Area" / "N.md", _nota("N", corpo="x\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    _write(vault / "Outra" / "N.md", _nota("N", corpo="x\n"))
    origem.unlink()
    rep = harvest(home, VAULT)
    assert rep.harvested == 1 and rep.removed == 1
    sks = set(_espelhos(home))
    assert any(k.endswith(":Outra/N.md") for k in sks)
    tombos = [m for m, b in _espelhos(home).values()
              if m.get("type") == "skill-tombstone"]
    assert any(t["source_key"].endswith(":Area/N.md") for t in tombos)


def test_vault_home_dentro_do_vault_nao_auto_colhe(tmp_path, monkeypatch):
    """NEURATA_HOME dentro da raiz: a subárvore do home é podada."""
    vault = tmp_path / "v"
    _write(vault / "Nota.md", _nota("Nota"))
    home = _home(vault / ".neurata")
    _write(home.library / "interna.md", _nota("Interna"))
    _set_vault(monkeypatch, vault)
    rep = harvest(home, VAULT)
    assert rep.harvested == 1  # só a nota do vault, a library não
    titulos = {m["title"] for m, _ in _espelhos(home).values()}
    assert "Interna" not in titulos


def test_vault_dentro_do_home_e_erro(tmp_path, monkeypatch):
    vault = tmp_path / "home" / "v"
    _write(vault / "Nota.md", _nota("Nota"))
    home = _home(tmp_path / "home")
    _set_vault(monkeypatch, vault)
    with pytest.raises(ValueError, match="NEURATA_HOME"):
        harvest(home, VAULT)


def test_vault_registry_e_resolve():
    from neurata.providers import REGISTRY, resolve
    assert "obsidian-vault" in REGISTRY
    assert resolve(VAULT) is _vault()


def test_vault_crlf_nao_re_emite(tmp_path, monkeypatch):
    """CRLF na fonte vira LF na leitura (walker): 2ª colheita é no-op."""
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    p = vault / "A.md"
    p.parent.mkdir(parents=True)
    p.write_bytes(b"prosa crlf\r\nlinha dois\r\n")
    _set_vault(monkeypatch, vault)
    first = harvest(home, VAULT)
    assert first.harvested == 1
    second = harvest(home, VAULT)
    assert (second.harvested, second.updated) == (0, 0)
    (_meta, body), = _espelhos(home).values()
    assert "\r" not in body


def test_vault_tags_viram_entry_tags_e_busca(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "Financas" / "Orcamento.md",
           _nota("Orcamento", corpo="Plano anual #orcamento.\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    from neurata.tick import curate_tick
    curate_tick(home)
    from neurata.query import query
    res = query(home, "plano tag:financas")
    assert res["results"], "busca por texto + tag de pasta tem que achar"
    assert res["results"][0]["slug"] == "orcamento"


def test_vault_alias_aresta_integracao(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "Fonte.md",
           _nota("Fonte", "aliases: [Apelido]\n", "Veja [[Apelido]].\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    from neurata.tick import curate_tick
    curate_tick(home)
    from neurata.indexdb import connect
    con = connect(home)
    try:
        n = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        con.close()
    assert n == 0  # self-link por alias não é aresta


def test_vault_alias_para_outro_grao_gera_aresta(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "A.md", _nota("A", corpo="fala do [[B-alias]].\n"))
    _write(vault / "B.md", _nota("B", "aliases: [B-alias]\n", "o alvo.\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    from neurata.tick import curate_tick
    curate_tick(home)
    from neurata.indexdb import connect
    con = connect(home)
    try:
        n = con.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
    finally:
        con.close()
    assert n == 1


# ── embed: `![[x]]` não é link ──────────────────────────────────────

def _lib_grao(home, slug: str, titulo: str, corpo: str) -> None:
    """Grão de library no padrão da casa: frontmatter com id/title, senão
    o reindex pula o arquivo e o teste mede um índice vazio."""
    (home.library / f"{slug}.md").write_text(
        f"---\nid: {slug.upper()}\ntitle: {titulo}\n---\n{corpo}\n",
        encoding="utf-8")


def test_embed_nao_gera_unresolved(tmp_path):
    home = _home(tmp_path / "home")
    _lib_grao(home, "nota", "Nota", "Imagem ![[foto.png]] inline.\n")
    stats = reindex(home)
    assert stats["indexed"] == 1       # o grão foi indexado de verdade
    assert stats["unresolved_links"] == 0
    assert stats["edges"] == 0


def test_wikilink_continua_gerando_aresta(tmp_path):
    home = _home(tmp_path / "home")
    _lib_grao(home, "a", "A", "veja [[B]].\n")
    _lib_grao(home, "b", "B", "alvo.\n")
    stats = reindex(home)
    assert stats["indexed"] == 2
    assert stats["edges"] == 1
    assert stats["unresolved_links"] == 0


def test_wikilink_pipe_e_hash_continuam_resolvendo(tmp_path):
    home = _home(tmp_path / "home")
    _lib_grao(home, "a", "A", "[[B|display]] e [[B#seção]] e ![[B]]\n")
    _lib_grao(home, "b", "B", "alvo.\n")
    stats = reindex(home)
    assert stats["indexed"] == 2
    assert stats["edges"] == 1          # B resolve nos 2 wikilinks (1 aresta)
    assert stats["unresolved_links"] == 0  # e o embed não conta


# ── contrato exclude_roots para providers root-scoped ────────────────

def test_project_scan_aceita_exclude_roots(tmp_path):
    from neurata.providers import project
    repo = tmp_path / "repo"
    _write(repo / "AGENTS.md", _nota("Agentes", corpo="regras.\n"))
    sub = repo / "sub"
    _write(sub / "CLAUDE.md", _nota("Claude", corpo="sub regras.\n"))
    itens, skipped = project.scan(repo, exclude_roots=(sub.resolve(),))
    assert {i.key for i in itens} == {"AGENTS.md"}
    assert skipped == []


def test_vault_scan_aceita_exclude_roots(tmp_path):
    vault = tmp_path / "v"
    _write(vault / "A.md", _nota("A"))
    sub = vault / "sub"
    _write(sub / "B.md", _nota("B"))
    itens, _ = _vault().scan(vault, exclude_roots=(sub.resolve(),))
    assert {i.key for i in itens} == {"A.md"}


# ── CLI ponta a ponta ──────────────────────────────────────────────

def test_vault_cli_dispatch(tmp_path, monkeypatch, capsys):
    from neurata.cli import main
    vault = tmp_path / "v"
    _write(vault / "Nota.md", _nota("Nota", corpo="comando CLI.\n"))
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path / "home"))
    _set_vault(monkeypatch, vault)
    rc = main(["harvest", VAULT, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["harvested"] == 1


def test_vault_sumido_e_erro_nao_tombstone(tmp_path, monkeypatch):
    """Vault removido (desmontado/renomeado) é erro, não colheita vazia.

    O genérico tem esse guard desde a v1.1 ("scan vazio + entries
    conhecidas = tombstone em tudo"); o provider nomeado precisa do
    mesmo — perder o disco não pode formatar a memória.
    """
    import shutil
    home = _home(tmp_path / "home")
    vault = tmp_path / "v"
    _write(vault / "N.md", _nota("N", corpo="conteudo.\n"))
    _set_vault(monkeypatch, vault)
    harvest(home, VAULT)
    from neurata.tick import curate_tick
    curate_tick(home)  # vault inteiro indexado
    shutil.rmtree(vault)
    with pytest.raises(ValueError, match="vault inexistente"):
        harvest(home, VAULT)
    assert _espelhos(home) == {}  # nada emitido: zero tombstones
