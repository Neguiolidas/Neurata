"""tests/test_absorb.py — absorção de edição feita na mão (v1.2, D7/D8).

O arquivo é a verdade; o índice é descartável. Editar um grão em
`library/` e rodar `tick` tem que fazer o índice servir o novo conteúdo,
sem trocar identidade, sem mexer em `source.*` e sem mover arquivo.
"""
import hashlib

from neurata.frontmatter import parse
from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.query import query
from neurata.reindex import reindex
from neurata.tick import curate_tick


def _home(tmp_path) -> NeurataHome:
    home = NeurataHome(tmp_path)
    home.init()
    reindex(home)
    return home


def _catalogado(home: NeurataHome, nome: str, corpo: str,
                extra: str = "") -> "tuple[str, str]":
    """Deposita via inbox e cataloga com um tick — o caminho real, para
    que o estado de partida seja o que o pipeline produz, não uma forja.
    Devolve (path relativo na library, id)."""
    (home.inbox / nome).write_text(
        f"---\ntitle: Original\n{extra}---\n{corpo}\n", encoding="utf-8")
    report = curate_tick(home)
    assert report.processed == 1, report.errors
    con = connect(home)
    try:
        rel, eid = con.execute(
            "SELECT path, id FROM entries WHERE location='library'"
        ).fetchone()
    finally:
        con.close()
    return rel, eid


def _reescreve(home: NeurataHome, rel: str, *, corpo=None, meta_extra="",
               **campos) -> None:
    """Reescreve o `.md` na mão, como um humano faria no editor."""
    caminho = home.root / rel
    meta, body = parse(caminho.read_text(encoding="utf-8"))
    meta.update(campos)
    linhas = [f"{k}: {v}" for k, v in meta.items()
              if not isinstance(v, (dict, list))]
    bloco = "\n".join(linhas) + "\n" + meta_extra
    caminho.write_text(f"---\n{bloco}---\n{corpo if corpo else body}\n",
                       encoding="utf-8")


def _linha(home: NeurataHome, eid: str) -> tuple:
    con = connect(home)
    try:
        return con.execute(
            "SELECT title, content_hash, path, slug FROM entries WHERE id=?",
            (eid,)).fetchone()
    finally:
        con.close()


def test_absorb_serves_new_body_and_updates_hash(tmp_path):
    """Critério 6: editar título + corpo, rodar tick, e o índice serve o
    novo — inclusive na busca FTS pelo termo que só existe agora."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    _reescreve(home, rel, corpo="corpo com xenoglossia", title="Editado")

    report = curate_tick(home)
    assert report.absorbed == 1
    titulo, chash, path, _slug = _linha(home, eid)
    assert titulo == "Editado"
    assert chash == hashlib.sha256(
        "corpo com xenoglossia\n".encode("utf-8")).hexdigest()
    assert path == rel  # não moveu
    assert [r["id"] for r in query(home, "xenoglossia")["results"]] == [eid]


def test_absorb_counts_in_absorbed_not_updated(tmp_path):
    """Critério 7: `updated` é update-in-place de source-keyed (§5). Uma
    edição na mão não é isso e não pode se disfarçar disso."""
    home = _home(tmp_path)
    rel, _eid = _catalogado(home, "a.md", "corpo velho")
    _reescreve(home, rel, corpo="corpo novo")

    report = curate_tick(home)
    assert (report.absorbed, report.updated, report.processed) == (1, 0, 0)
    assert report.quarantined == 0 and report.errors == []


def test_absorb_preserves_source_block(tmp_path):
    """Critério 8: procedência é do depósito, não da varredura."""
    home = _home(tmp_path)
    rel, _eid = _catalogado(
        home, "a.md", "corpo velho",
        extra="source:\n  agent: hermes\n  session: s-1\n  origin: manual\n")
    antes, _ = parse((home.root / rel).read_text(encoding="utf-8"))
    _reescreve(home, rel, corpo="corpo novo",
               meta_extra="source:\n  agent: hermes\n  session: s-1\n"
                          "  origin: manual\n")

    curate_tick(home)
    depois, _ = parse((home.root / rel).read_text(encoding="utf-8"))
    assert depois["source"] == antes["source"]


def test_absorb_preserves_identity_fields(tmp_path):
    """Invariante 6: `id`, `slug`, `path` e `created` não mudam — trocar
    slug por causa de um título editado quebraria links por estética."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    antes, _ = parse((home.root / rel).read_text(encoding="utf-8"))
    _linha_antes = _linha(home, eid)
    _reescreve(home, rel, corpo="corpo novo", title="Editado")

    curate_tick(home)
    depois, _ = parse((home.root / rel).read_text(encoding="utf-8"))
    for campo in ("id", "slug", "path", "created"):
        assert depois.get(campo) == antes.get(campo)
    assert _linha(home, eid)[2:] == _linha_antes[2:]  # path, slug


def test_second_tick_absorbs_nothing_and_does_not_touch_file(tmp_path):
    """Critério 9 / invariante 7: idempotência sai de graça porque o hash
    é do corpo — reescrever o frontmatter não realimenta o laço."""
    home = _home(tmp_path)
    rel, _eid = _catalogado(home, "a.md", "corpo velho")
    _reescreve(home, rel, corpo="corpo novo")
    curate_tick(home)

    caminho = home.root / rel
    antes = (caminho.stat().st_mtime_ns,
             caminho.read_text(encoding="utf-8"))
    report = curate_tick(home)
    assert report.absorbed == 0
    assert (caminho.stat().st_mtime_ns,
            caminho.read_text(encoding="utf-8")) == antes


def test_absorb_refuses_when_id_changed(tmp_path):
    """Critério 13: id trocado não é edição, é troca de identidade.
    Absorver seria trocar de grão em silêncio — o passo passa adiante."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    _hash_antes = _linha(home, eid)[1]
    _reescreve(home, rel, corpo="corpo novo", id="01OUTROIDDIFERENTE000000000")

    report = curate_tick(home)
    assert report.absorbed == 0
    assert _linha(home, eid)[1] == _hash_antes


def test_absorb_skips_broken_frontmatter(tmp_path):
    """Julgar integridade é trabalho do `doctor`, que já reporta esses
    arquivos. Repetir o diagnóstico a cada tick só produziria ruído."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    _hash_antes = _linha(home, eid)[1]
    (home.root / rel).write_text("---\ntitle: [\n---\ncorpo novo\n",
                                 encoding="utf-8")

    report = curate_tick(home)
    assert (report.absorbed, report.errors) == (0, [])
    assert _linha(home, eid)[1] == _hash_antes


def test_absorb_clears_stale_marks(tmp_path):
    """Sem isso, `doctor` continuaria acusando divergência de freshness
    depois de um tick que já absorveu."""
    home = _home(tmp_path)
    rel, _eid = _catalogado(home, "a.md", "corpo velho")
    _reescreve(home, rel, corpo="corpo novo", stale="true",
               stale_since="2026-01-01T00:00:00+00:00")

    curate_tick(home)
    meta, _ = parse((home.root / rel).read_text(encoding="utf-8"))
    assert "stale" not in meta and "stale_since" not in meta


def test_absorb_writes_journal_entry(tmp_path):
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    _reescreve(home, rel, corpo="corpo novo")
    curate_tick(home)

    recs = [r for r in home.read_log("journal") if r.get("verb") == "absorb"]
    assert len(recs) == 1
    assert recs[0]["item"] == eid and recs[0]["dst"] == rel


def test_absorb_verb_is_not_a_catalog_verb():
    """`absorb` explica uma mutação, não a presença do arquivo na library.
    Entrasse em `_CATALOG_VERBS`, um órfão absorvido nunca mais passaria
    pela checagem de identidade do passo 3."""
    from neurata.tick import _CATALOG_VERBS
    assert "absorb" not in _CATALOG_VERBS


def test_absorption_before_orphan_pass_prevents_spurious_quarantine(tmp_path):
    """D7: hoje uma entrada órfã **e** editada falha o `hash_ok` do passo
    3 e é purgada + quarentenada. Absorver antes sincroniza o hash e
    deixa a decisão de (3) recair só sobre a identidade."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho")
    (home.root / "logs" / "journal.jsonl").write_text("", encoding="utf-8")
    _reescreve(home, rel, corpo="corpo novo")

    report = curate_tick(home)
    assert report.absorbed == 1
    assert report.quarantined == 0
    assert report.reconciled == 1
    assert (home.root / rel).is_file()


def test_absorb_ignores_mirrors(tmp_path):
    """Espelho é cache de fonte externa: a superfície de edição é o vault,
    não o `.md` da library, e re-hashear 14 mil espelhos por tick comeria
    o orçamento sub-segundo sozinho."""
    home = _home(tmp_path)
    rel, eid = _catalogado(home, "a.md", "corpo velho",
                           extra="source_key: obsidian:vault/x\n")
    hash_antes = _linha(home, eid)[1]
    _reescreve(home, rel, corpo="corpo novo",
               meta_extra="source_key: obsidian:vault/x\n")

    report = curate_tick(home)
    assert report.absorbed == 0
    assert _linha(home, eid)[1] == hash_antes
