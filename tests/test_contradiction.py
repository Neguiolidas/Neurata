"""tests/test_contradiction.py — v1.5 Contradição de verdade, ponta a ponta.

O exemplo do dono governa o ciclo inteiro: "Use sqlite" e "Nunca use
sqlite" nunca se emparelhariam por Jaccard (quase-duplicata); aqui eles
nascem como pares, aparecem na busca e se resolvem por supersessão com
procedência. Determinístico, journaled, sem LLM.
"""
import pytest

from neurata import indexdb
from neurata.deposit import deposit
from neurata.doctor import run_checks
from neurata.home import CONTRACT_VERSION, NeurataHome
from neurata.indexdb import connect
from neurata.query import QueryError, query
from neurata.reindex import reindex
from neurata.supersede import (
    SupersedeError,
    contradictions_report,
    resolve_all,
    supersede,
)
from neurata.tick import curate_tick


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _dep(home, texto, **kw):
    r = deposit(home, content=texto, title=kw.pop("title", None), **kw)
    curate_tick(home)
    return r["id"]


def _pairs(home):
    con = connect(home)
    try:
        return con.execute(
            "SELECT a_id, b_id, target, a_pol, b_pol FROM"
            " contradictions").fetchall()
    finally:
        con.close()


def _assertions(home):
    con = connect(home)
    try:
        return con.execute(
            "SELECT entry_id, target, polarity FROM assertions"
            " ORDER BY entry_id, target").fetchall()
    finally:
        con.close()


# ── detecção no tick ─────────────────────────────────────────────────

def test_par_do_roadmap_detectado_no_tick(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")

    pares = _pairs(home)
    assert len(pares) == 1
    a_id, b_id, target, a_pol, b_pol = pares[0]
    assert {a_id, b_id} == {pos, neg}
    assert target == "sqlite"
    assert {a_pol, b_pol} == {"pos", "neg"}


def test_journal_registra_verb_contradiction(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use redis para a fila.\n")
    _dep(home, "Evite redis para a fila.\n")
    verbos = [r.get("verb") for r in home.read_log("journal")]
    assert "contradiction" in verbos
    rec = next(r for r in home.read_log("journal")
               if r["verb"] == "contradiction")
    assert rec["target"] == "redis"
    assert rec["polarity"] in ("pos", "neg")
    assert rec["opponent"]


def test_mesma_polaridade_nao_e_par(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Prefira sqlite no ambiente local.\n")
    assert _pairs(home) == []


def test_mirror_nao_participa(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use sqlite para o cache local.\n")
    # espelho: entra pela rota source-keyed com proibição do mesmo alvo
    (home.inbox / "m.md").write_text(
        "---\nid: 01MIRROR\ntitle: Espelho\nsource_key: foo:bar\n"
        "---\nNunca use sqlite para o cache local.\n", encoding="utf-8")
    curate_tick(home)
    assert _pairs(home) == []


def test_corpo_que_muda_de_polaridade_apaga_par_antigo(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Nunca use sqlite para o cache local.\n")
    assert len(_pairs(home)) == 1
    # edita o pos na mão: corpo vira proibição também — concordância
    import neurata.frontmatter as fm
    for p in home.library.glob("*.md"):
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if str(m["id"]) == pos:
            p.write_text(
                f"---\nid: {pos}\ntitle: T\n---\n"
                "Nunca use sqlite para o cache local.\n", encoding="utf-8")
    rep = curate_tick(home)
    assert rep.absorbed == 1
    assert _pairs(home) == []


def test_tick_idempotente_nao_replica_par(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Nunca use sqlite para o cache local.\n")
    assert len(_pairs(home)) == 1
    antes = len([r for r in home.read_log("journal")
                 if r["verb"] == "contradiction"])
    rep = curate_tick(home)  # inbox vazio: nada a fazer
    assert rep.contradictions == 0
    depois = len([r for r in home.read_log("journal")
                  if r["verb"] == "contradiction"])
    assert antes == depois


# ── reindex full ─────────────────────────────────────────────────────

def test_reindex_reconstrui_afirmacoes_e_marcador(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Evite sqlite em producao.\n")
    r = reindex(home)
    assert r["contradictions"] == 1
    con = connect(home)
    try:
        built = con.execute("SELECT value FROM meta WHERE"
                            " key='assertions_built'").fetchone()[0]
    finally:
        con.close()
    assert built == "1"
    assert len(_assertions(home)) == 2


def test_entry_purge_limpa_afirmacoes_e_pares(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Nunca use sqlite para o cache local.\n")
    con = connect(home)
    try:
        rowid = con.execute("SELECT rowid FROM entries WHERE id=?",
                            (pos,)).fetchone()[0]
        indexdb.entry_purge(con, rowid, pos)
        con.commit()
        rest = con.execute("SELECT COUNT(*) FROM contradictions"
                           ).fetchone()[0]
        af = con.execute("SELECT COUNT(*) FROM assertions WHERE"
                         " entry_id=?", (pos,)).fetchone()[0]
    finally:
        con.close()
    assert rest == 0
    assert af == 0


# ── consumo na query ─────────────────────────────────────────────────

def _card_of(home, qstr, eid):
    out = query(home, qstr)
    return next(c for c in out["results"] if c["id"] == eid)


def test_query_anota_contradicao_aberta(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    c_pos = _card_of(home, "sqlite", pos)
    c_neg = _card_of(home, "sqlite", neg)
    assert {"id": neg, "target": "sqlite", "polarity": "pos"} in \
        c_pos["contradicts"]
    assert {"id": pos, "target": "sqlite", "polarity": "neg"} in \
        c_neg["contradicts"]
    assert c_pos["superseded_by"] is None


def test_superseded_afunda_e_carrega_vencedor(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    supersede(home, neg, pos)  # proibição perde, recomendação vence

    out = query(home, "sqlite")
    ids = [c["id"] for c in out["results"]]
    assert ids[-1] == neg  # afundado
    perdedor = next(c for c in out["results"] if c["id"] == neg)
    assert perdedor["superseded_by"] == pos
    assert perdedor["contradicts"] == []  # par fechou


def test_facet_status_superseded(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    supersede(home, neg, pos)
    out = query(home, "status:superseded")
    assert [c["id"] for c in out["results"]] == [neg]
    with pytest.raises(QueryError):
        query(home, "status:banana")


# ── supersessão ──────────────────────────────────────────────────────

def test_supersede_escreve_no_arquivo_e_journal(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    r = supersede(home, neg, pos)
    assert r["action"] == "superseded"
    # arquivo é a verdade: marca no frontmatter
    from neurata.entryref import resolve
    entry = resolve(home, neg)
    assert entry.meta["superseded_by"] == pos
    verbos = [x["verb"] for x in home.read_log("journal")]
    assert verbos.count("supersede") == 1
    # índice derivou
    con = connect(home)
    try:
        (sb,) = con.execute("SELECT superseded_by FROM entries WHERE id=?",
                            (neg,)).fetchone()
    finally:
        con.close()
    assert sb == pos


def test_supersede_noop_e_erros(tmp_path):
    from neurata.entryref import EntryNotFoundError

    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    assert supersede(home, neg, pos)["action"] == "superseded"
    assert supersede(home, neg, pos)["action"] == "noop"
    with pytest.raises(SupersedeError):
        supersede(home, neg, neg)
    with pytest.raises(EntryNotFoundError):
        supersede(home, "nao-existe", pos)
    # espelho é insubstituível
    (home.inbox / "m.md").write_text(
        "---\nid: 01MIRROR\ntitle: Espelho\nsource_key: foo:bar\n---\nx\n",
        encoding="utf-8")
    curate_tick(home)
    with pytest.raises(SupersedeError):
        supersede(home, "01MIRROR", pos)


def test_resolve_all_regra_recencia_e_classe(tmp_path):
    home = _home(tmp_path)
    # par 1: mesma classe (episódico default) — updated mais novo vence
    a = _dep(home, "Use postgres versao quinze.\n", title="a")
    b = _dep(home, "Nunca use postgres versao quinze.\n", title="b")
    # par 2: episódico perde para semântico, mesmo sendo mais novo
    c = _dep(home, "Use redis para a fila.\n", title="c")
    d = _dep(home, "Evite redis para a fila.\n", title="d")
    _forca_datas(home, {a: "2026-01-01T00:00:00+00:00",
                        b: "2026-02-01T00:00:00+00:00",
                        c: "2026-03-01T00:00:00+00:00",
                        d: "2026-01-01T00:00:00+00:00"})
    _forca_classe(home, {d: "semantic"})
    reindex(home)

    r = resolve_all(home)
    assert r["resolved_count"] == 2
    marcados = _superseded_map(home)
    assert marcados.get(a) == b   # recência
    assert marcados.get(c) == d   # classe: semântico sobrevive ao evento


def _superseded_map(home):
    import neurata.frontmatter as fm
    out = {}
    for p in home.library.glob("*.md"):
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if m.get("superseded_by"):
            out[str(m["id"])] = str(m["superseded_by"])
    return out


def _forca_datas(home, por_id):
    import neurata.frontmatter as fm
    for p in home.library.glob("*.md"):
        m, body = fm.parse(p.read_text(encoding="utf-8"))
        eid = str(m.get("id"))
        if eid in por_id:
            m["updated"] = por_id[eid]
            m["created"] = por_id[eid]
            p.write_text(fm.serialize(m, body), encoding="utf-8")


def _forca_classe(home, por_id):
    import neurata.frontmatter as fm
    for p in home.library.glob("*.md"):
        m, body = fm.parse(p.read_text(encoding="utf-8"))
        eid = str(m.get("id"))
        if eid in por_id:
            m["class"] = por_id[eid]
            p.write_text(fm.serialize(m, body), encoding="utf-8")


# ── report + doctor ──────────────────────────────────────────────────

def test_contradictions_report_conta_abertos_e_resolvidos(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    neg = _dep(home, "Nunca use sqlite para o cache local.\n")
    rep = contradictions_report(home)
    assert rep["open_count"] == 1 and rep["total"] == 1
    supersede(home, neg, pos)
    rep = contradictions_report(home)
    assert rep["open_count"] == 0
    assert rep["resolved_count"] == 1
    assert rep["assertions"] == 2


def test_doctor_alerta_deteccao_nao_construida(tmp_path):
    home = _home(tmp_path)
    _dep(home, "Use sqlite para o cache local.\n")
    # tick incrementou assertions, mas a varredura full nunca rodou
    checks = {c.name: c for c in run_checks(home)}
    assert checks["contradictions"].status == "warn"
    reindex(home)
    checks = {c.name: c for c in run_checks(home)}
    assert checks["contradictions"].status == "ok"
    # e o contrato segue coerente com a constante importada
    assert CONTRACT_VERSION == 5


def test_absorb_que_rederiva_mesmo_par_nao_reloga(tmp_path):
    home = _home(tmp_path)
    pos = _dep(home, "Use sqlite para o cache local.\n")
    _dep(home, "Nunca use sqlite para o cache local.\n")
    n0 = len([r for r in home.read_log("journal")
              if r["verb"] == "contradiction"])
    assert n0 == 1

    # corpo muda (absorb roda), a afirmação permanece: o par é
    # restaurado em silêncio — history não repete noticia
    import neurata.frontmatter as fm
    for p in home.library.glob("*.md"):
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if str(m["id"]) == pos:
            p.write_text(
                f"---\nid: {pos}\ntitle: T\n---\n"
                "Use sqlite para o cache local.\n\nLinha nova de contexto.\n",
                encoding="utf-8")
    rep = curate_tick(home)
    assert rep.absorbed == 1
    assert rep.contradictions == 0
    n1 = len([r for r in home.read_log("journal")
              if r["verb"] == "contradiction"])
    assert n1 == n0
    assert len(_pairs(home)) == 1
