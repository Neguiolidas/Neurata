"""tests/test_colheita.py — v1.8 critérios de aceite, ponta a ponta.

Do roadmap (medições 2026-09-06): entry_tags > 0; faceta `tag:` casa;
espelhos com class ≠ NULL; stale no índice; efeito medido nos DOIS
lados (busca padrão exclui morto; --include-stale devolve); 2ª
re-colheita é no-op.
"""

from neurata.deposit import deposit
from neurata.harvest import harvest
from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.query import query
from neurata.tick import curate_tick


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _repo_com_tags(tmp_path):
    repo = tmp_path / "fonte"
    (repo / "skills" / "coletor").mkdir(parents=True, exist_ok=True)
    (repo / "skills" / "coletor" / "SKILL.md").write_text(
        "---\nname: coletor-osint\ndescription: Coleta sinais de osint\n"
        "tags: [osint, honeypot, backdoor]\n---\n"
        "Coleta sinais publicos de superficies expostas.\n",
        encoding="utf-8")
    (repo / "notas.md").write_text(
        "---\ntitle: Notas gerais\ntags: [geral]\naliases: [anotacoes]\n---\n"
        "Notas soltas do time.\n", encoding="utf-8")
    return repo


def _repo_sem_tags(tmp_path):
    repo = tmp_path / "fonte"
    (repo / "skills" / "coletor").mkdir(parents=True, exist_ok=True)
    (repo / "skills" / "coletor" / "SKILL.md").write_text(
        "---\nname: coletor-osint\ndescription: Coleta sinais de osint\n"
        "---\nColeta sinais publicos de superficies expostas.\n",
        encoding="utf-8")
    (repo / "notas.md").write_text(
        "---\ntitle: Notas gerais\n---\nNotas soltas do time.\n",
        encoding="utf-8")
    return repo


def _indice(home):
    con = connect(home)
    try:
        rows = con.execute(
            "SELECT source_key, class, stale FROM entries"
            " WHERE source_key LIKE 'fonte@%' AND location='library'"
            " ORDER BY source_key").fetchall()
        tags = dict(con.execute(
            "SELECT e.source_key, COUNT(t.tag) FROM entries e"
            " JOIN entry_tags t ON t.entry_rowid = e.rowid"
            " WHERE e.source_key LIKE 'fonte@%' GROUP BY e.source_key"))
    finally:
        con.close()
    return rows, tags


def test_aceite_colheita_tag_classe_stale(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    repo = _repo_sem_tags(tmp_path)

    # ciclo antigo: colheita SEM tags, tick, e um grão morre na fonte
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo))
    harvest(home, "fonte", source_dir=repo)
    curate_tick(home)
    (repo / "notas.md").unlink()
    harvest(home, "fonte", source_dir=repo)
    curate_tick(home)

    rows, tags = _indice(home)
    por_key = {sk: (cls, st) for sk, cls, st in rows}
    sk_skill = next(k for k in por_key if k.endswith("SKILL.md"))
    sk_notas = next(k for k in por_key if k.endswith("notas.md"))
    assert por_key[sk_notas][1] == "true"      # stale no índice (v1.8)
    assert tags.get(sk_skill, 0) == 0          # colheita antiga: zero tags

    # a fonte GANHA tags; a re-colheita tem que carregar os três campos
    repo2 = _repo_com_tags(tmp_path)
    for rel in ("skills/coletor/SKILL.md", "notas.md"):
        src = (repo2 / rel).read_text(encoding="utf-8")
        alvo = repo / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(src, encoding="utf-8")
    rep = harvest(home, "fonte", source_dir=repo)
    assert rep.updated == 2   # skill ganha tags; notas RENASCE do
    # tombstone já com tags (era stale no índice, volta sem)
    assert rep.harvested == 0
    curate_tick(home)

    rows, tags = _indice(home)
    por_key = {sk: (cls, st) for sk, cls, st in rows}
    # CRITÉRIO 1+2: entry_tags > 0 e a faceta tag: casa
    assert tags.get(sk_skill, 0) == 3
    out = query(home, "tag:osint")
    assert [c["slug"] for c in out["results"]]
    # CRITÉRIO 3: espelho com class != NULL
    assert por_key[sk_skill][0] == "procedural"
    assert por_key[sk_notas][0] == "semantic"
    # CRITÉRIO 4: stale no índice...
    assert por_key[sk_notas][1] != "true"      # renasceu: stale limpo
    (repo / "notas.md").unlink()
    harvest(home, "fonte", source_dir=repo)
    curate_tick(home)
    rows, _tags = _indice(home)
    por_key = {sk: (cls, st) for sk, cls, st in rows}
    assert por_key[sk_notas][1] == "true"

    # CRITÉRIO 5: efeito medido nos DOIS lados
    out_dead = query(home, "notas gerais")
    assert [c for c in out_dead["results"]
            if c["slug"] == "notas-gerais"] == []
    out_live = query(home, "notas gerais", include_stale=True)
    assert any(c["slug"] == "notas-gerais" for c in out_live["results"])


def test_segunda_recolheita_e_noop(tmp_path, monkeypatch):
    home = _home(tmp_path / "home")
    repo = _repo_com_tags(tmp_path)
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo))
    harvest(home, "fonte", source_dir=repo)
    curate_tick(home)
    rep2 = harvest(home, "fonte", source_dir=repo)
    assert rep2.harvested == 0 and rep2.updated == 0 and rep2.removed == 0
    curate_tick(home)
    rep3 = harvest(home, "fonte", source_dir=repo)
    assert rep3.harvested == 0 and rep3.updated == 0


def test_tombstone_de_fonte_sumida_gera_stale_no_indice(
        tmp_path, monkeypatch):
    """Os 875 órfãos do roadmap: a RAIZ existe, os arquivos sumiram
    dentro dela — removed_keys pega, tombstone emite, o índice recebe."""
    home = _home(tmp_path / "home")
    repo = _repo_com_tags(tmp_path)
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo))
    harvest(home, "fonte", source_dir=repo)
    curate_tick(home)
    (repo / "skills" / "coletor" / "SKILL.md").unlink()
    rep = harvest(home, "fonte", source_dir=repo)
    assert rep.removed == 1
    curate_tick(home)
    con = connect(home)
    try:
        rows = con.execute(
            "SELECT stale FROM entries WHERE source_key LIKE"
            " 'fonte@%SKILL.md' AND location='library'").fetchall()
    finally:
        con.close()
    assert rows and rows[0][0] == "true"


def test_busca_sem_stale_tem_ranking_intacto(tmp_path):
    """Regressão: acervo sem nenhum stale — a exclusão é no-op e a
    ordem fica idêntica."""
    home = _home(tmp_path / "home")
    deposit(home, content="Use sqlite para o cache local.", title="a")
    deposit(home, content="Nunca use sqlite sem WAL.", title="b")
    curate_tick(home)
    o1 = query(home, "sqlite")["results"]
    o2 = query(home, "sqlite", include_stale=True)["results"]
    assert [c["id"] for c in o1] == [c["id"] for c in o2]
