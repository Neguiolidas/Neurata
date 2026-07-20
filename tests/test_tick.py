"""tests/test_tick.py — inbox → library, curadoria mecânica (spec v0.4 tick).

Cobre §1 (pipeline geral), §3 (renames/órfãos/entradas mortas) e §4
(near-dup). Ver docs/superpowers/specs/2026-07-18-neurata-v0.4-tick.md.
"""
import hashlib
import os

import pytest

from neurata.frontmatter import parse
from neurata.home import NeurataHome
from neurata.indexdb import connect
from neurata.reindex import reindex
from neurata.tick import TickReport, TickStructuralError, curate_tick
from neurata.tick import _sync_update_in_place


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    reindex(home)  # índice vazio, mas com schema válido (meta gravada)
    return home


def _inbox(home, name, text):
    (home.inbox / name).write_text(text, encoding="utf-8")


def _lib_entry(home, filename, entry_id, title, body):
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    text = (f"---\nid: {entry_id}\ntitle: {title}\n"
            f"content_hash: {content_hash}\n---\n{body}")
    (home.library / filename).write_text(text, encoding="utf-8")
    return content_hash


def _journal(home):
    return home.read_log("journal")


# ── §1 pipeline geral ────────────────────────────────────────────────

def test_budget_cuts_and_rerun_completes(tmp_path):
    home = _home(tmp_path)
    for i in range(3):
        _inbox(home, f"item{i}.md",
              f"# Nota {i}\n\nCorpo da nota numero {i} com texto o "
              "bastante pra nao ficar vazio no teste.\n")
    report1 = curate_tick(home, budget=2)
    assert report1.processed == 2
    assert len(list(home.inbox.glob("*.md"))) == 1
    report2 = curate_tick(home)
    assert report2.processed == 1
    assert list(home.inbox.glob("*.md")) == []
    assert len(list(home.library.glob("*.md"))) == 3


def test_symlink_outside_inbox_quarantined_target_untouched(tmp_path):
    home = _home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.md"
    target.write_text("# Segredo\n\nConteudo sensivel fora do inbox.\n")
    link = home.inbox / "link.md"
    link.symlink_to(target)

    report = curate_tick(home)

    assert report.quarantined == 1
    assert len(report.errors) == 1
    assert "guard" in report.errors[0].reason
    assert not link.exists()
    assert (home.quarantine / "link.md").is_symlink()
    assert target.read_text() == "# Segredo\n\nConteudo sensivel fora do inbox.\n"


def test_unparseable_frontmatter_alphabetized_body_verbatim(tmp_path):
    home = _home(tmp_path)
    original = "---\nsem terminador\nmais linhas que nao fecham o bloco\n"
    _inbox(home, "quebrado.md", original)

    report = curate_tick(home)

    assert report.literate == 1
    assert report.processed == 1
    dest = next(home.library.glob("*.md"))
    meta, body = parse(dest.read_text())
    assert body == original
    assert meta["grain_quality"] == "mechanical"


def test_alphabetize_body_byte_exact_no_frontmatter(tmp_path):
    home = _home(tmp_path)
    original = "Só um parágrafo cru — sem frontmatter: café, ação, coração.\n"
    _inbox(home, "cru.md", original)

    curate_tick(home)

    dest = next(home.library.glob("*.md"))
    _, body = parse(dest.read_text())
    assert body == original


def test_body_untouched_when_frontmatter_already_present(tmp_path):
    home = _home(tmp_path)
    body = "Corpo com **markdown** e acentos: ação, café — nunca muda.\n"
    _inbox(home, "item.md",
          f"---\nid: 01ITEMID0000000000000000\ntitle: Item\n---\n{body}")

    curate_tick(home)

    dest = next(home.library.glob("*.md"))
    _, got_body = parse(dest.read_text())
    assert got_body == body


def test_journal_paths_are_relative(tmp_path):
    home = _home(tmp_path)
    _inbox(home, "a.md",
          "# A\n\nCorpo com texto suficiente pra nao ficar vazio no teste.\n")

    curate_tick(home)

    journal = _journal(home)
    catalog = next(r for r in journal if r["verb"] == "catalog")
    assert catalog["src"] == "inbox/a.md"
    assert catalog["dst"].startswith("library/")
    assert not catalog["src"].startswith("/")
    assert not catalog["dst"].startswith("/")


def test_journal_write_failure_recorded_as_error(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _inbox(home, "a.md",
          "# A\n\nCorpo com texto suficiente pra nao ficar vazio aqui.\n")

    def boom(name, record):
        raise OSError("disco cheio")

    monkeypatch.setattr(home, "append_log", boom)

    report = curate_tick(home)

    assert report.processed == 0
    assert len(report.errors) == 1
    assert "journal" in report.errors[0].reason


def test_stale_index_schema_raises_structural_error(tmp_path):
    home = _home(tmp_path)
    _inbox(home, "a.md",
          "# A\n\nCorpo com texto suficiente pra nao ficar vazio aqui.\n")
    con = connect(home)
    con.execute(
        "UPDATE meta SET value=? WHERE key='index_schema_version'", ("4",))
    con.commit()
    con.close()

    with pytest.raises(TickStructuralError, match="reindex"):
        curate_tick(home)

    assert (home.inbox / "a.md").exists()
    assert list(home.library.glob("*.md")) == []


def test_cross_device_raises_before_touching_inbox(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _inbox(home, "a.md",
          "# A\n\nCorpo com texto suficiente pra nao ficar vazio aqui.\n")
    real_stat = os.stat

    class _FakeStat:
        def __init__(self, dev):
            self.st_dev = dev

    def fake_stat(path, *a, **kw):
        if str(path) == str(home.library):
            return _FakeStat(999999)
        return real_stat(path, *a, **kw)

    monkeypatch.setattr("neurata.tick.os.stat", fake_stat)

    with pytest.raises(TickStructuralError):
        curate_tick(home)

    assert (home.inbox / "a.md").exists()
    assert list(home.library.glob("*.md")) == []


# ── §4 near-dup ──────────────────────────────────────────────────────

def test_exact_duplicate_content_hash_quarantined(tmp_path):
    home = _home(tmp_path)
    body = "Este é o corpo exato que será duplicado no inbox mais tarde.\n"
    _lib_entry(home, "orig.md", "01LIBEXACT0000000000000", "Original", body)
    reindex(home)
    _inbox(home, "dup.md",
          f"---\nid: 01DUP00000000000000000\ntitle: Duplicado\n---\n{body}")

    report = curate_tick(home)

    assert report.quarantined == 1
    assert report.processed == 0
    assert (home.quarantine / "dup.md").exists()


def test_near_dup_above_threshold_marks_conflict(tmp_path):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    body = " ".join(words) + ".\n"
    lib_id = "01LIBBASEID0000000000000"
    _lib_entry(home, "base.md", lib_id, "Base", body)
    reindex(home)
    inbox_body = " ".join(words + ["extra1", "extra2"]) + ".\n"
    _inbox(home, "novo.md",
          f"---\nid: 01NEWWITHNEARDUP000000\ntitle: Novo\n---\n{inbox_body}")

    report = curate_tick(home)

    assert report.processed == 1
    assert report.conflicts == 1
    dest = next(home.library.glob("novo*.md"))
    meta, _ = parse(dest.read_text())
    assert meta["conflicts_with"] == [lib_id]


def test_near_dup_below_threshold_no_mark(tmp_path):
    home = _home(tmp_path)
    lib_words = [f"palavra{i}" for i in range(30)]
    _lib_entry(home, "base.md", "01LIBBASEID0000000000000", "Base",
              " ".join(lib_words) + ".\n")
    reindex(home)
    other_words = [f"outra{i}" for i in range(30)]
    _inbox(home, "novo.md",
          "---\nid: 01NEWDIFFERENT000000000\ntitle: Novo\n---\n"
          + " ".join(other_words) + ".\n")

    report = curate_tick(home)

    assert report.processed == 1
    assert report.conflicts == 0
    dest = next(home.library.glob("novo*.md"))
    meta, _ = parse(dest.read_text())
    assert "conflicts_with" not in meta


def test_near_dup_tie_picks_smaller_ulid(tmp_path):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    body = " ".join(words) + ".\n"
    _lib_entry(home, "b.md", "01BBBBBBBBBBBBBBBBBBBBBB", "B entry maior", body)
    _lib_entry(home, "a.md", "01AAAAAAAAAAAAAAAAAAAAAA", "A entry menor", body)
    reindex(home)
    inbox_body = " ".join(words + ["extra1", "extra2"]) + ".\n"
    _inbox(home, "novo.md",
          f"---\nid: 01NOVO0000000000000000\ntitle: Novo\n---\n{inbox_body}")

    report = curate_tick(home)

    assert report.conflicts == 1
    dest = next(home.library.glob("novo*.md"))
    meta, _ = parse(dest.read_text())
    assert meta["conflicts_with"] == ["01AAAAAAAAAAAAAAAAAAAAAA"]


def test_existing_conflicts_with_not_rejournaled(tmp_path):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    body = " ".join(words) + ".\n"
    lib_id = "01LIBBASEID0000000000000"
    _lib_entry(home, "base.md", lib_id, "Base", body)
    reindex(home)
    inbox_body = " ".join(words + ["extra1", "extra2"]) + ".\n"
    text = (f"---\nid: 01NEWWITHMARK00000000\ntitle: Marcado\n"
           f"conflicts_with: [{lib_id}]\n---\n{inbox_body}")
    _inbox(home, "novo.md", text)

    report = curate_tick(home)

    assert report.processed == 1
    assert report.conflicts == 0
    journal = _journal(home)
    assert not any(r["verb"] == "conflict" for r in journal)


def test_slug_collision_appends_suffix(tmp_path):
    home = _home(tmp_path)
    _lib_entry(home, "nota.md", "01EXISTING00000000000000", "Nota",
              "Corpo original ja catalogado com bastante texto aqui.\n")
    reindex(home)
    _inbox(home, "novo.md",
          "---\nid: 01NEWID000000000000000\ntitle: Nota\n---\n"
          "Corpo novo bem diferente sobre outro assunto qualquer aqui.\n")

    curate_tick(home)

    assert (home.library / "nota-2.md").exists()


# ── §3 renames / órfãos / entradas mortas ────────────────────────────

def test_human_rename_reconciled(tmp_path):
    home = _home(tmp_path)
    _lib_entry(home, "antigo.md", "01REN0000000000000000", "Antigo",
              "Corpo estavel que nao muda entre reindex e rename.\n")
    reindex(home)
    (home.library / "antigo.md").rename(home.library / "novo-nome.md")

    report = curate_tick(home)

    assert report.renamed == 1
    con = connect(home)
    row = con.execute(
        "SELECT path FROM entries WHERE id='01REN0000000000000000'"
    ).fetchone()
    assert row[0] == "library/novo-nome.md"
    rename_events = [r for r in _journal(home) if r["verb"] == "rename"]
    assert rename_events[0]["src"] == "library/antigo.md"
    assert rename_events[0]["dst"] == "library/novo-nome.md"


def test_orphan_file_adopted(tmp_path):
    home = _home(tmp_path)
    (home.library / "orfao.md").write_text(
        "---\nid: 01ORFAO000000000000000\ntitle: Orfao\n---\n"
        "Arquivo que apareceu em library fora do indice, sem par.\n")

    report = curate_tick(home)

    assert report.processed == 1
    con = connect(home)
    row = con.execute(
        "SELECT path FROM entries WHERE id='01ORFAO000000000000000'"
    ).fetchone()
    assert row is not None and row[0] == "library/orfao.md"
    assert any(r["verb"] == "catalog" and r["item"] == "01ORFAO000000000000000"
              for r in _journal(home))


def test_dead_entry_removed_when_file_missing_and_no_hash_pair(tmp_path):
    home = _home(tmp_path)
    (home.library / "vai-sumir.md").write_text(
        "---\nid: 01DEAD0000000000000000\ntitle: Vai Sumir\n---\n"
        "Este arquivo sera removido do disco antes do tick rodar aqui.\n")
    reindex(home)
    (home.library / "vai-sumir.md").unlink()

    report = curate_tick(home)

    assert any("entrada morta" in e.reason for e in report.errors)
    con = connect(home)
    assert con.execute(
        "SELECT 1 FROM entries WHERE id='01DEAD0000000000000000'"
    ).fetchone() is None
    assert any(r["verb"] == "error" and r["item"] == "01DEAD0000000000000000"
              for r in _journal(home))


def test_missing_entries_paired_deterministically(tmp_path):
    home = _home(tmp_path)
    body = "Corpo compartilhado por duas entradas renomeadas ao mesmo tempo.\n"
    _lib_entry(home, "x1.md", "01BBB0000000000000000", "X1", body)
    _lib_entry(home, "x2.md", "01AAA0000000000000000", "X2", body)
    reindex(home)
    (home.library / "x1.md").rename(home.library / "y-second.md")
    (home.library / "x2.md").rename(home.library / "y-first.md")

    report = curate_tick(home)

    assert report.renamed == 2
    con = connect(home)
    rows = dict(con.execute(
        "SELECT id, path FROM entries WHERE id IN "
        "('01AAA0000000000000000','01BBB0000000000000000')").fetchall())
    # menor ULID (01AAA...) pareia com o primeiro candidato em ordem
    # alfabética de caminho ("y-first" < "y-second").
    assert rows["01AAA0000000000000000"] == "library/y-first.md"
    assert rows["01BBB0000000000000000"] == "library/y-second.md"


# ── Fase 5 (v0.5 harvest) — reconciliação source-keyed ───────────────

def _skill_item(home, filename, entry_id, source_key, title, body,
                description="desc"):
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    text = (f"---\nid: {entry_id}\ntype: skill\nenv: claude-code\n"
           f"title: {title}\ndescription: {description}\n"
           f"source_key: {source_key}\nsource_path: /tmp/{title}\n"
           f"created: 2026-01-01T00:00:00+00:00\n"
           f"content_hash: {content_hash}\n---\n{body}")
    (home.inbox / filename).write_text(text, encoding="utf-8")
    return content_hash


def _tombstone_item(home, filename, entry_id, source_key):
    text = (f"---\nid: {entry_id}\ntype: skill-tombstone\n"
           f"source_key: {source_key}\n"
           f"created: 2026-01-01T00:00:00+00:00\n---\n")
    (home.inbox / filename).write_text(text, encoding="utf-8")


def _lib_skill_entry(home, filename, entry_id, source_key, title, body,
                     description="desc", extra_meta=""):
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    text = (f"---\nid: {entry_id}\ntype: skill\nenv: claude-code\n"
           f"title: {title}\ndescription: {description}\n"
           f"source_key: {source_key}\nsource_path: /tmp/{title}\n"
           f"created: 2026-01-01T00:00:00+00:00\n"
           f"updated: 2026-01-01T00:00:00+00:00\n"
           f"content_hash: {content_hash}\n{extra_meta}---\n{body}")
    (home.library / filename).write_text(text, encoding="utf-8")
    return content_hash


def test_skill_item_new_cataloged_with_source_key_no_near_dup(tmp_path):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    similar_body = " ".join(words) + ".\n"
    _lib_entry(home, "base.md", "01LIBBASEID0000000000000", "Base",
              similar_body)
    reindex(home)
    skill_body = " ".join(words + ["extra1", "extra2"]) + ".\n"
    _skill_item(home, "skill.md", "01SKILLNEW0000000000000",
               "claude-code:foo", "Foo Skill", skill_body)

    report = curate_tick(home)

    assert report.processed == 1
    assert report.conflicts == 0
    con = connect(home)
    row = con.execute(
        "SELECT source_key, type FROM entries WHERE"
        " id='01SKILLNEW0000000000000'").fetchone()
    assert row == ("claude-code:foo", "skill")
    assert list(home.inbox.glob("*.md")) == []
    journal = _journal(home)
    assert any(r["verb"] == "catalog" and
              r["item"] == "01SKILLNEW0000000000000" for r in journal)


def test_skill_item_noop_when_hash_matches_library(tmp_path):
    home = _home(tmp_path)
    body = "Corpo estavel do skill sem mudanca nenhuma aqui.\n"
    _lib_skill_entry(home, "foo.md", "01SKILLLIB000000000000",
                    "claude-code:foo", "Foo Skill", body)
    reindex(home)
    original = (home.library / "foo.md").read_text()
    _skill_item(home, "foo-new.md", "01SKILLNEWID00000000000",
               "claude-code:foo", "Foo Skill", body)

    report = curate_tick(home)

    assert report.processed == 0
    assert report.updated == 0
    assert list(home.inbox.glob("*.md")) == []
    assert (home.library / "foo.md").read_text() == original
    assert _journal(home) == []


def test_skill_item_update_in_place_preserves_id_slug_path(tmp_path):
    home = _home(tmp_path)
    old_body = "Corpo antigo do skill antes da atualizacao aqui.\n"
    entry_id = "01SKILLUPDATE00000000000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", old_body)
    reindex(home)
    new_body = "Corpo novo do skill depois da atualizacao mudou aqui.\n"
    _skill_item(home, "foo-new.md", "01SKILLINBOXNEW00000000",
               "claude-code:foo", "Foo Skill Renomeado", new_body,
               description="nova desc")

    report = curate_tick(home)

    assert report.updated == 1
    assert list(home.inbox.glob("*.md")) == []
    dest = home.library / "foo.md"
    assert dest.exists()
    meta, body = parse(dest.read_text())
    assert body == new_body
    assert meta["id"] == entry_id
    assert meta["title"] == "Foo Skill Renomeado"
    assert meta["description"] == "nova desc"
    new_hash = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    assert meta["content_hash"] == new_hash
    con = connect(home)
    row = con.execute(
        "SELECT path, content_hash FROM entries WHERE id=?",
        (entry_id,)).fetchone()
    assert row == ("library/foo.md", new_hash)
    journal = _journal(home)
    upd = next(r for r in journal if r["verb"] == "update")
    assert upd["src"] == "inbox/foo-new.md"
    assert upd["dst"] == "library/foo.md"


def test_skill_item_update_preserves_extra_frontmatter_keys(tmp_path):
    home = _home(tmp_path)
    old_body = "Corpo antigo do skill com chaves extras no frontmatter.\n"
    entry_id = "01SKILLEXTRA000000000000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", old_body,
                    extra_meta="conflicts_with: [01OUTRO000000000000000]\n"
                               "cataloged: 2026-01-01T00:00:00+00:00\n"
                               "origin: manual\n")
    reindex(home)
    new_body = "Corpo novo do skill que muda mas mantem chaves extras.\n"
    _skill_item(home, "foo-new.md", "01SKILLINBOXEXTRA0000000",
               "claude-code:foo", "Foo Skill", new_body)

    report = curate_tick(home)

    assert report.updated == 1
    meta, body = parse((home.library / "foo.md").read_text())
    assert body == new_body
    assert meta["conflicts_with"] == ["01OUTRO000000000000000"]
    assert meta["cataloged"] == "2026-01-01T00:00:00+00:00"
    assert meta["origin"] == "manual"


def test_sync_update_in_place_requires_source_key(tmp_path):
    home = _home(tmp_path)
    con = connect(home)
    report = TickReport(tick="t")
    with pytest.raises(AssertionError):
        _sync_update_in_place(
            home, con, "t", entry_id="x", slug="x",
            lib_rel_path="library/x.md", source_key=None,
            new_meta={}, new_body="", inbox_path=home.inbox / "x.md",
            rel_src="inbox/x.md", report=report, shingle_sets={})


def test_tombstone_marks_library_entry_stale(tmp_path):
    home = _home(tmp_path)
    body = "Corpo do skill que vai ficar obsoleto na fonte agora.\n"
    entry_id = "01SKILLSTALE00000000000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", body)
    reindex(home)
    _tombstone_item(home, "tomb.md", "01TOMBID0000000000000000",
                    "claude-code:foo")

    report = curate_tick(home)

    assert report.stale == 1
    assert list(home.inbox.glob("*.md")) == []
    meta, got_body = parse((home.library / "foo.md").read_text())
    assert got_body == body
    assert meta["stale"] == "true"
    assert "stale_since" in meta
    journal = _journal(home)
    assert any(r["verb"] == "stale" and r["item"] == entry_id
              for r in journal)


def test_tombstone_nonexistent_source_key_noop(tmp_path):
    home = _home(tmp_path)
    _tombstone_item(home, "tomb.md", "01TOMBGHOST000000000000",
                    "claude-code:inexistente")

    report = curate_tick(home)

    assert report.stale == 0
    assert report.errors == []
    assert list(home.inbox.glob("*.md")) == []
    journal = _journal(home)
    assert any(r["verb"] == "noop" for r in journal)


def test_skill_renaissance_after_tombstone_clears_stale(tmp_path):
    home = _home(tmp_path)
    old_body = "Corpo original antes do skill ficar obsoleto aqui mesmo.\n"
    entry_id = "01SKILLRENASCE000000000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", old_body)
    reindex(home)
    _tombstone_item(home, "tomb.md", "01TOMBRENASCE00000000000",
                    "claude-code:foo")
    curate_tick(home)
    meta, _ = parse((home.library / "foo.md").read_text())
    assert meta["stale"] == "true"

    new_body = "Corpo novo depois que a skill foi re-colhida de novo.\n"
    _skill_item(home, "foo-again.md", "01SKILLAGAIN000000000000",
               "claude-code:foo", "Foo Skill", new_body)

    report = curate_tick(home)

    assert report.updated == 1
    assert report.processed == 0
    meta2, body2 = parse((home.library / "foo.md").read_text())
    assert body2 == new_body
    assert "stale" not in meta2
    assert "stale_since" not in meta2
    journal = _journal(home)
    updates = [r for r in journal if r["verb"] == "update"]
    assert len(updates) == 1
