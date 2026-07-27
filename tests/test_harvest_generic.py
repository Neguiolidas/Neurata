"""tests/test_harvest_generic.py — harvest end-to-end pelo provider genérico.

O que aqui importa é o contrato de *identidade* do item colhido de um
diretório: `source_key = <target>@<hash do dir>:<caminho relativo>`. É
por ele que o harvest decide o que é novo, o que mudou e o que virou
tombstone — e diferente do provider nomeado, ele não depende do título
do documento.

O `@<hash>` sai de `_keys()` nas asserções de rotina (ruído: depende do
tmp_path), mas tem teste próprio — ele é o que separa duas fontes de
mesmo basename.
"""
import hashlib
import re

import pytest

from neurata.frontmatter import parse
from neurata.harvest import harvest
from neurata.home import NeurataHome


def _mkhome(tmp_path):
    home = NeurataHome(tmp_path / "neurata")
    home.init()
    return home


def _mktree(root):
    """Um diretório com um formato de cada — o caso de uso do PRD."""
    (root / "skills" / "alpha").mkdir(parents=True)
    (root / "skills" / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: faz alpha\n---\nCorpo alpha.\n",
        encoding="utf-8")
    (root / "notes.md").write_text(
        "# Nota Solta\n\nPrimeiro parágrafo vira descrição.\n",
        encoding="utf-8")
    (root / "agent.yaml").write_text(
        "name: agente-yaml\ndescription: agente de teste\n", encoding="utf-8")
    (root / ".cursorrules").write_text(
        "Sempre responda em português.\n", encoding="utf-8")
    return root


def _inbox_items(home):
    """(meta, body) de cada item do inbox, ordenado por source_key."""
    out = []
    for path in sorted(home.inbox.glob("*.md")):
        meta, body = parse(path.read_text(encoding="utf-8"))
        out.append((meta, body))
    return sorted(out, key=lambda mb: mb[0]["source_key"])


def _norm(source_key):
    """source_key com o `@<hash do dir>` colapsado, pra asserção legível."""
    return re.sub(r"@[0-9a-f]{12}:", ":", source_key, count=1)


def _keys(home):
    return [_norm(meta["source_key"]) for meta, _ in _inbox_items(home)]


def test_harvest_generic_colhe_todos_os_formatos(tmp_path):
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")

    report = harvest(home, "docs", source_dir=src, fmt="auto")

    assert report.harvested == 4
    assert report.updated == 0
    assert report.removed == 0
    assert _keys(home) == [
        "docs:.cursorrules",
        "docs:agent.yaml",
        "docs:notes.md",
        "docs:skills/alpha/SKILL.md",
    ]


def test_harvest_generic_content_hash_bate_com_corpo(tmp_path):
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")

    harvest(home, "docs", source_dir=src, fmt="auto")

    for meta, body in _inbox_items(home):
        expected = hashlib.sha256(body.encode("utf-8")).hexdigest()
        assert meta["content_hash"] == expected, meta["source_key"]
        assert meta["env"] == "docs"
        assert meta["type"] == "skill"


def test_harvest_generic_rescan_nao_duplica(tmp_path):
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")

    harvest(home, "docs", source_dir=src, fmt="auto")
    before = _keys(home)
    report = harvest(home, "docs", source_dir=src, fmt="auto")

    assert report.harvested == 0
    assert report.updated == 0
    assert report.removed == 0
    assert _keys(home) == before


def test_harvest_generic_source_key_sobrevive_a_renomear_titulo(tmp_path):
    """Editar o `name:` não cria item novo — a identidade é o caminho."""
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")
    harvest(home, "docs", source_dir=src, fmt="auto")

    skill = src / "skills" / "alpha" / "SKILL.md"
    skill.write_text(
        "---\nname: alpha-renomeada\ndescription: faz alpha\n---\n"
        "Corpo alpha v2.\n", encoding="utf-8")
    report = harvest(home, "docs", source_dir=src, fmt="auto")

    # `updated` só conta contra a library; como nada foi depositado ainda,
    # a revisão reentra no inbox como item novo — mas sob a MESMA chave, e
    # sem tombstone (que é o que provaria identidade quebrada).
    assert report.removed == 0
    keys = _keys(home)
    assert "docs:skills/alpha-renomeada" not in keys
    assert keys.count("docs:skills/alpha/SKILL.md") == 2
    novo = [mb for mb in _inbox_items(home)
            if _norm(mb[0]["source_key"]) == "docs:skills/alpha/SKILL.md"]
    assert any(meta["title"] == "alpha-renomeada" for meta, _ in novo)


def test_harvest_generic_arquivo_removido_vira_tombstone(tmp_path):
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")
    harvest(home, "docs", source_dir=src, fmt="auto")

    (src / "notes.md").unlink()
    report = harvest(home, "docs", source_dir=src, fmt="auto")

    assert report.removed == 1
    tombs = [meta for meta, _ in _inbox_items(home)
             if meta["type"] == "skill-tombstone"]
    assert [_norm(m["source_key"]) for m in tombs] == ["docs:notes.md"]


def test_harvest_generic_tombstone_nao_reemite(tmp_path):
    home = _mkhome(tmp_path)
    src = _mktree(tmp_path / "src")
    harvest(home, "docs", source_dir=src, fmt="auto")
    (src / "notes.md").unlink()
    harvest(home, "docs", source_dir=src, fmt="auto")

    report = harvest(home, "docs", source_dir=src, fmt="auto")

    assert report.removed == 0


def test_harvest_generic_targets_distintos_nao_se_canibalizam(tmp_path):
    """Dois diretórios sob targets diferentes convivem no mesmo inbox."""
    home = _mkhome(tmp_path)
    a = _mktree(tmp_path / "a")
    b = tmp_path / "b"
    b.mkdir()
    (b / "outro.md").write_text("# Outro\n\nTexto.\n", encoding="utf-8")

    harvest(home, "a", source_dir=a, fmt="auto")
    report = harvest(home, "b", source_dir=b, fmt="auto")

    assert report.harvested == 1
    assert report.removed == 0
    assert "a:notes.md" in _keys(home)
    assert "b:outro.md" in _keys(home)


def test_harvest_generic_mesmo_basename_nao_colide(tmp_path):
    """`a/sub` e `b/sub` são fontes distintas sob o mesmo target default.

    Regressão: o namespace era só o target, e o CLI usa o basename do
    path como target default — as duas fontes viravam `sub:doc.md` e
    disputavam a mesma chave. Sintoma: recolher uma redepositava o item
    (o pending do inbox devolvia o hash da outra), duplicando no inbox.
    """
    home = _mkhome(tmp_path)
    a = tmp_path / "a" / "sub"
    b = tmp_path / "b" / "sub"
    for d, txt in ((a, "Lado A."), (b, "Lado B, diferente.")):
        d.mkdir(parents=True)
        (d / "doc.md").write_text(f"# Doc\n\n{txt}\n", encoding="utf-8")

    harvest(home, "sub", source_dir=a, fmt="auto")
    harvest(home, "sub", source_dir=b, fmt="auto")

    # nenhum canibaliza o outro...
    reharvest = harvest(home, "sub", source_dir=a, fmt="auto")
    assert reharvest.removed == 0
    # ...e recolher é idempotente, sem duplicar no inbox
    assert reharvest.harvested == 0
    assert reharvest.updated == 0

    keys = [meta["source_key"] for meta, _ in _inbox_items(home)]
    assert len(keys) == 2
    assert len(set(keys)) == 2, f"fontes distintas com a mesma chave: {keys}"


def test_harvest_generic_namespace_e_estavel_entre_caminhos_equivalentes(
        tmp_path):
    """Mesmo diretório real por caminho diferente = mesma fonte."""
    home = _mkhome(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "um.md").write_text("# Um\n\nTexto.\n", encoding="utf-8")

    harvest(home, "src", source_dir=src, fmt="auto")
    report = harvest(home, "src", source_dir=tmp_path / "." / "src",
                     fmt="auto")

    assert report.harvested == 0
    assert report.removed == 0


def test_harvest_generic_fmt_fixo_ignora_auto_detect(tmp_path):
    home = _mkhome(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "um.md").write_text("# Um\n\nTexto um.\n", encoding="utf-8")
    (src / "dois.md").write_text("# Dois\n\nTexto dois.\n", encoding="utf-8")

    report = harvest(home, "docs", source_dir=src, fmt="markdown")

    assert report.harvested == 2
    assert _keys(home) == ["docs:dois.md", "docs:um.md"]


def test_harvest_claude_code_inalterado_por_source_dir(tmp_path):
    """Provider nomeado continua chaveando por nome, não por caminho."""
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: faz alpha\n---\nCorpo.\n",
        encoding="utf-8")

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.harvested == 1
    assert _keys(home) == ["claude-code:alpha-skill"]


def test_harvest_generic_source_dir_inexistente(tmp_path):
    home = _mkhome(tmp_path)

    with pytest.raises(ValueError, match=r"[Dd]iretório"):
        harvest(home, "docs", source_dir=tmp_path / "nao-existe", fmt="auto")


# --- review hostil: invariantes do namespace de source_key ---

def test_harvest_target_com_dois_pontos_e_rejeitado(tmp_path):
    """':' no target faria um alvo emitir tombstone nos itens de outro."""
    home = _mkhome(tmp_path)
    src = tmp_path / "p"
    src.mkdir()
    (src / "a.md").write_text("# A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="separador"):
        harvest(home, "proj:sub", source_dir=src, fmt="markdown")


@pytest.mark.parametrize("target", ["", "   "])
def test_harvest_target_vazio_e_rejeitado(tmp_path, target):
    home = _mkhome(tmp_path)
    src = tmp_path / "p"
    src.mkdir()
    (src / "a.md").write_text("# A\n", encoding="utf-8")
    with pytest.raises(ValueError, match="vazio"):
        harvest(home, target, source_dir=src, fmt="markdown")


def test_alvos_irmaos_nao_se_canibalizam(tmp_path):
    """Alvo 'proj' não pode tombstonar item colhido sob outro alvo."""
    home = _mkhome(tmp_path)
    root = tmp_path / "proj"
    (root / "sub").mkdir(parents=True)
    (root / "a.md").write_text("# A\n", encoding="utf-8")
    (root / "sub" / "b.md").write_text("# B\n", encoding="utf-8")
    harvest(home, "sub", source_dir=root / "sub", fmt="markdown")
    rep = harvest(home, "proj", source_dir=root, fmt="markdown")
    assert rep.removed == 0


# --- auto-ingestão -------------------------------------------------
# Colher de dentro do próprio home re-ingere a Library: o acervo dobra
# a cada rodada (1→2→4→8...), cada clone com id/source_key novos.

def test_harvest_rejeita_source_dir_igual_ao_home(tmp_path):
    home = _mkhome(tmp_path)

    with pytest.raises(ValueError, match="dentro do NEURATA_HOME"):
        harvest(home, "self", source_dir=home.root)


def test_harvest_rejeita_source_dir_dentro_do_home(tmp_path):
    home = _mkhome(tmp_path)

    with pytest.raises(ValueError, match="dentro do NEURATA_HOME"):
        harvest(home, "self", source_dir=home.root / "library")


def test_harvest_poda_home_quando_ele_esta_sob_a_raiz(tmp_path):
    """`harvest ~/` é legítimo: colhe o resto e ignora o próprio home."""
    home = _mkhome(tmp_path)
    (tmp_path / "doc.md").write_text("# Doc\n", encoding="utf-8")
    (home.root / "library" / "plantado.md").write_text(
        "# Plantado\n", encoding="utf-8")

    report = harvest(home, "pai", source_dir=tmp_path)

    assert report.harvested == 1
    assert [k.split(":")[-1] for k in _keys(home)] == ["doc.md"]


def test_harvest_do_pai_e_idempotente(tmp_path):
    """Sem a poda, cada rodada re-colheria os envelopes da rodada anterior."""
    home = _mkhome(tmp_path)
    (tmp_path / "doc.md").write_text("# Doc\n", encoding="utf-8")

    harvest(home, "pai", source_dir=tmp_path)
    segunda = harvest(home, "pai", source_dir=tmp_path)

    assert (segunda.harvested, segunda.updated, segunda.removed) == (0, 0, 0)
