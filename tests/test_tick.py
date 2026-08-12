"""tests/test_tick.py — inbox → library, curadoria mecânica (spec v0.4 tick).

Cobre §1 (pipeline geral), §3 (renames/órfãos/entradas mortas) e §4
(near-dup). Ver docs/superpowers/specs/2026-07-18-neurata-v0.4-tick.md.
"""
import hashlib
import os

import pytest
from conftest import forge_v7_entries, insert_entry, set_index_version

from neurata.frontmatter import parse
from neurata.home import NeurataHome
from neurata.indexdb import connect, fts_insert
from neurata.query import query
from neurata.reindex import reindex
from neurata.snapshot import list_snapshots
from neurata.tick import (
    TickReport,
    TickStructuralError,
    _sync_update_in_place,
    curate_tick,
)
from neurata.tick import _journal as _write_journal


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


# ── TickReport.snapshot (v0.6, T3) ──────────────────────────────────

def test_tick_report_snapshot_field_defaults_to_none():
    report = TickReport(tick="01JTICK0000000000000000000")
    assert report.snapshot is None


def test_tick_report_snapshot_field_accepts_sha_string():
    report = TickReport(tick="01JTICK0000000000000000000",
                        snapshot="abc123def456")
    assert report.snapshot == "abc123def456"


def test_curate_tick_commits_snapshot_when_tree_dirty(tmp_path):
    # T4: curate_tick chama ensure_repo+commit_tick dentro do IndexLock,
    # após o con.close(). Árvore suja (novo arquivo na library) -> commita
    # e popula report.snapshot com o sha curto do commit.
    home = _home(tmp_path)
    _inbox(home, "a.md", "# Nota\n\nCorpo qualquer com texto suficiente.\n")
    report = curate_tick(home)
    assert report.snapshot is not None
    assert len(report.snapshot) == 12
    assert (home.library / ".git").exists()


def test_curate_tick_snapshot_is_none_when_tree_clean(tmp_path):
    # Segundo tick sem novidade no inbox: nada muda na library (já
    # commitada pelo tick anterior) -> commit_tick vê tree limpa e
    # devolve None, sem novo commit.
    home = _home(tmp_path)
    _inbox(home, "a.md", "# Nota\n\nCorpo qualquer com texto suficiente.\n")
    first = curate_tick(home)
    assert first.snapshot is not None
    second = curate_tick(home)
    assert second.snapshot is None


def test_curate_tick_snapshot_failure_is_best_effort_and_logged(
        tmp_path, monkeypatch):
    # Falha do lado do git (ex.: commit_tick levanta) não pode derrubar o
    # tick — best-effort loud: report.snapshot cai pra None e o incidente
    # fica registrado em logs/snapshot.jsonl (com o tick_id do run).
    home = _home(tmp_path)
    _inbox(home, "a.md", "# Nota\n\nCorpo qualquer com texto suficiente.\n")

    def boom(home_arg, report_arg):
        raise RuntimeError("git explodiu")

    monkeypatch.setattr("neurata.tick.commit_tick", boom)
    report = curate_tick(home)

    assert report.snapshot is None
    assert report.processed == 1  # o resto do tick correu normalmente
    entries = home.read_log("snapshot")
    assert len(entries) == 1
    assert entries[0]["tick"] == report.tick
    assert "git explodiu" in entries[0]["error"]


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


# ── T1: guard path absoluto/`..` no log de dedup (journal) ─────────

def test_journal_rejects_absolute_src_path(tmp_path):
    home = _home(tmp_path)
    report = TickReport(tick="01JTICK0000000000000000000")

    ok = _write_journal(home, report.tick, "catalog", "id1",
                        "/etc/passwd", "library/a.md", report)

    assert ok is False
    assert _journal(home) == []
    assert len(report.errors) == 1


def test_journal_rejects_dotdot_component_in_dst_path(tmp_path):
    home = _home(tmp_path)
    report = TickReport(tick="01JTICK0000000000000000000")

    ok = _write_journal(home, report.tick, "catalog", "id1",
                        "inbox/a.md", "../../etc/passwd", report)

    assert ok is False
    assert _journal(home) == []
    assert len(report.errors) == 1


def test_journal_rejects_mixed_dotdot_path(tmp_path):
    home = _home(tmp_path)
    report = TickReport(tick="01JTICK0000000000000000000")

    ok = _write_journal(home, report.tick, "catalog", "id1",
                        "a/../../b", "library/a.md", report)

    assert ok is False
    assert _journal(home) == []
    assert len(report.errors) == 1


def test_journal_accepts_legit_relative_path(tmp_path):
    home = _home(tmp_path)
    report = TickReport(tick="01JTICK0000000000000000000")

    ok = _write_journal(home, report.tick, "catalog", "id1",
                        "inbox/a.md", "library/a.md", report)

    assert ok is True
    assert report.errors == []
    journal = _journal(home)
    assert len(journal) == 1
    assert journal[0]["src"] == "inbox/a.md"
    assert journal[0]["dst"] == "library/a.md"


# ── T1-fix: call-sites de _journal() devem checar o retorno (Important
#    #1 da revisão de T1) — se _journal() falhar (guard ou I/O), o
#    contador de sucesso correspondente do report NÃO pode subir, e
#    nenhuma entrada falsa/incompleta deve aparecer no journal. ─────

def _fail_append_log_for_verb(monkeypatch, home, target_verb):
    """Faz home.append_log falhar (OSError) só pro record com o verb
    indicado — os demais verbs do mesmo tick continuam sendo gravados
    normalmente. Simula _journal() retornando False num call-site
    específico sem mexer nos outros (guard e OSError são só as duas
    causas possíveis de _journal() retornar False; o bug de
    dessincronia é o mesmo pros callers nos dois casos)."""
    real_append_log = home.append_log

    def fake(name, record):
        if name == "journal" and record.get("verb") == target_verb:
            raise OSError(f"falha simulada de I/O para verb={target_verb!r}")
        return real_append_log(name, record)

    monkeypatch.setattr(home, "append_log", fake)


def test_literate_journal_failure_does_not_increment_literate_counter(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    original = "---\nsem terminador\nmais linhas que nao fecham o bloco\n"
    _inbox(home, "quebrado.md", original)
    _fail_append_log_for_verb(monkeypatch, home, "literate")

    report = curate_tick(home)

    assert report.processed == 1  # catalog (verb diferente) gravou ok
    assert report.literate == 0
    assert any("falha ao gravar journal" in e.reason for e in report.errors)
    journal = _journal(home)
    assert any(r["verb"] == "catalog" for r in journal)
    assert not any(r["verb"] == "literate" for r in journal)


def test_conflict_journal_failure_does_not_increment_conflict_counter(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    body = " ".join(words) + ".\n"
    lib_id = "01LIBBASEID0000000000000"
    _lib_entry(home, "base.md", lib_id, "Base", body)
    reindex(home)
    inbox_body = " ".join([*words, "extra1", "extra2"]) + ".\n"
    _inbox(home, "novo.md",
          f"---\nid: 01NEWWITHNEARDUP000000\ntitle: Novo\n---\n{inbox_body}")
    _fail_append_log_for_verb(monkeypatch, home, "conflict")

    report = curate_tick(home)

    # processed == 2: base.md (fixture indexado via reindex direto, sem
    # journal) é reconciliado como órfão write-then-log (T2) + novo.md
    # cataloged normalmente — ambos contam em processed (T2 fix).
    assert report.processed == 2
    assert report.conflicts == 0
    dest = next(home.library.glob("novo*.md"))
    meta, _ = parse(dest.read_text())
    # marca ficou gravada no frontmatter (ação física já ocorrida antes
    # do journal), mas o journal/contador não pode mentir sobre isso.
    assert meta["conflicts_with"] == [lib_id]
    journal = _journal(home)
    assert not any(r["verb"] == "conflict" for r in journal)


def test_update_journal_failure_does_not_increment_updated_counter(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    old_body = "Corpo antigo do skill antes da atualizacao aqui.\n"
    entry_id = "01SKILLUPDATEFAIL000000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", old_body)
    reindex(home)
    new_body = "Corpo novo do skill depois da atualizacao mudou aqui.\n"
    _skill_item(home, "foo-new.md", "01SKILLINBOXFAIL0000000",
               "claude-code:foo", "Foo Skill Renomeado", new_body)
    _fail_append_log_for_verb(monkeypatch, home, "update")

    report = curate_tick(home)

    assert report.updated == 0
    # ação física (write na library + unlink do inbox) já ocorreu antes
    # do journal: bug seria contador mentir mesmo assim.
    dest = home.library / "foo.md"
    _, got_body = parse(dest.read_text())
    assert got_body == new_body
    assert list(home.inbox.glob("*.md")) == []
    journal = _journal(home)
    assert not any(r["verb"] == "update" for r in journal)


def test_stale_journal_guard_rejection_does_not_increment_stale_counter(
        tmp_path):
    home = _home(tmp_path)
    body = "Corpo do skill que ficara obsoleto com path corrompido no indice.\n"
    entry_id = "01SKILLSTALECORRUPT0000"
    _lib_skill_entry(home, "foo.md", entry_id, "claude-code:foo",
                    "Foo Skill", body)
    reindex(home)
    # Corrompe o path indexado: sobrevive ao resolve do SO (".." cancela
    # antes de "library", o arquivo é lido normalmente), mas contém
    # componente ".." literal — exatamente o dado "malicioso" vindo do
    # indexdb que o guard de _journal deve rejeitar no dst (cenário
    # citado na revisão como o de maior risco depois de quarantine).
    con = connect(home)
    con.execute("UPDATE entries SET path=? WHERE id=?",
               ("library/../library/foo.md", entry_id))
    con.commit()
    con.close()
    _tombstone_item(home, "tomb.md", "01TOMBCORRUPT00000000000",
                    "claude-code:foo")

    report = curate_tick(home)

    assert report.stale == 0
    meta, _ = parse((home.library / "foo.md").read_text())
    assert meta.get("stale") == "true"  # ação física já ocorreu
    assert "stale_since" in meta
    assert any("path suspeito" in e.reason for e in report.errors)
    journal = _journal(home)
    assert not any(r["verb"] == "stale" for r in journal)


def test_quarantine_journal_failure_does_not_increment_quarantined_counter(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    body = "Este e o corpo exato que sera duplicado no inbox mais tarde.\n"
    _lib_entry(home, "orig.md", "01LIBEXACT0000000000000", "Original", body)
    reindex(home)
    _inbox(home, "dup.md",
          f"---\nid: 01DUP00000000000000000\ntitle: Duplicado\n---\n{body}")
    _fail_append_log_for_verb(monkeypatch, home, "quarantine")

    report = curate_tick(home)

    assert report.quarantined == 0
    # ação física (rename pra quarantine/) já ocorreu antes do journal —
    # exatamente o pior caso apontado na revisão: história com buraco
    # silencioso enquanto o relatório afirmaria sucesso.
    assert (home.quarantine / "dup.md").exists()
    assert not (home.inbox / "dup.md").exists()
    assert any("falha ao gravar journal" in e.reason for e in report.errors)
    journal = _journal(home)
    assert not any(r["verb"] == "quarantine" for r in journal)


def test_rename_journal_failure_does_not_increment_renamed_counter(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    _lib_entry(home, "antigo.md", "01REN0000000000000000", "Antigo",
              "Corpo estavel que nao muda entre reindex e rename.\n")
    reindex(home)
    (home.library / "antigo.md").rename(home.library / "novo-nome.md")
    _fail_append_log_for_verb(monkeypatch, home, "rename")

    report = curate_tick(home)

    assert report.renamed == 0
    con = connect(home)
    row = con.execute(
        "SELECT path FROM entries WHERE id='01REN0000000000000000'"
    ).fetchone()
    # índice já foi atualizado (estado > história) mesmo sem journal:
    assert row[0] == "library/novo-nome.md"
    journal = _journal(home)
    assert not any(r["verb"] == "rename" for r in journal)


def test_orphan_catalog_journal_failure_does_not_increment_processed(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    (home.library / "orfao.md").write_text(
        "---\nid: 01ORFAOFAIL0000000000\ntitle: Orfao\n---\n"
        "Arquivo que apareceu em library fora do indice, sem par.\n")
    _fail_append_log_for_verb(monkeypatch, home, "catalog")

    report = curate_tick(home)

    assert report.processed == 0
    con = connect(home)
    row = con.execute(
        "SELECT path FROM entries WHERE id='01ORFAOFAIL0000000000'"
    ).fetchone()
    # o órfão já foi adotado no índice mesmo sem entrada no journal:
    assert row is not None and row[0] == "library/orfao.md"
    journal = _journal(home)
    assert not any(r["verb"] == "catalog" for r in journal)


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
        """stat real com `st_dev` trocado.

        Os demais campos precisam continuar verdadeiros: `neurata.tick.os`
        É o módulo `os` global, então o patch abaixo vale pro processo
        inteiro, e o `glob()` das asserções finais também passa por aqui.
        Quais campos o pathlib lê varia por versão (3.11/3.12 leem
        `st_mode` em `is_dir()`), então delegamos em vez de adivinhar.
        """

        def __init__(self, real, dev):
            self._real = real
            self.st_dev = dev

        def __getattr__(self, name):
            return getattr(self._real, name)

    def fake_stat(path, *a, **kw):
        real = real_stat(path, *a, **kw)
        if str(path) == str(home.library):
            return _FakeStat(real, real.st_dev + 1)
        return real

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
    # processed == 1: o duplicado em si é quarentenado (não processa),
    # mas orig.md (fixture indexado via reindex direto, sem journal) é
    # reconciliado como órfão write-then-log (T2) e conta em processed.
    assert report.processed == 1
    assert (home.quarantine / "dup.md").exists()


def test_near_dup_above_threshold_marks_conflict(tmp_path):
    home = _home(tmp_path)
    words = [f"palavra{i}" for i in range(30)]
    body = " ".join(words) + ".\n"
    lib_id = "01LIBBASEID0000000000000"
    _lib_entry(home, "base.md", lib_id, "Base", body)
    reindex(home)
    inbox_body = " ".join([*words, "extra1", "extra2"]) + ".\n"
    _inbox(home, "novo.md",
          f"---\nid: 01NEWWITHNEARDUP000000\ntitle: Novo\n---\n{inbox_body}")

    report = curate_tick(home)

    # processed == 2: base.md (fixture indexado via reindex direto, sem
    # journal) é reconciliado como órfão write-then-log (T2) + novo.md
    # cataloged normalmente.
    assert report.processed == 2
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

    # processed == 2: base.md (fixture indexado via reindex direto, sem
    # journal) é reconciliado como órfão write-then-log (T2) + novo.md
    # cataloged normalmente.
    assert report.processed == 2
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
    inbox_body = " ".join([*words, "extra1", "extra2"]) + ".\n"
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
    inbox_body = " ".join([*words, "extra1", "extra2"]) + ".\n"
    text = (f"---\nid: 01NEWWITHMARK00000000\ntitle: Marcado\n"
           f"conflicts_with: [{lib_id}]\n---\n{inbox_body}")
    _inbox(home, "novo.md", text)

    report = curate_tick(home)

    # processed == 2: base.md (fixture indexado via reindex direto, sem
    # journal) é reconciliado como órfão write-then-log (T2) + novo.md
    # cataloged normalmente.
    assert report.processed == 2
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
    skill_body = " ".join([*words, "extra1", "extra2"]) + ".\n"
    _skill_item(home, "skill.md", "01SKILLNEW0000000000000",
               "claude-code:foo", "Foo Skill", skill_body)

    report = curate_tick(home)

    # processed == 2: base.md (fixture indexado via reindex direto, sem
    # journal) é reconciliado como órfão write-then-log (T2) + skill.md
    # cataloged normalmente.
    assert report.processed == 2
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

    # processed == 1: o skill-item em si é um noop puro (nada de
    # update/catalog pra ele); mas foo.md (fixture indexado via
    # reindex() direto, sem passar pelo tick antes) é reconciliado como
    # órfão write-then-log (T2) na primeira curadoria, e isso conta em
    # processed (mesmo padrão dos demais pontos de catalogação).
    assert report.processed == 1
    assert report.updated == 0
    assert list(home.inbox.glob("*.md")) == []
    assert (home.library / "foo.md").read_text() == original
    journal = _journal(home)
    assert len(journal) == 1
    assert journal[0]["verb"] == "catalog"
    assert journal[0]["item"] == "01SKILLLIB000000000000"
    assert journal[0].get("reconciled") == "write-then-log"


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


# ── T2: reconciliação de órfão write-then-log ───────────────────────
# Órfão write-then-log: escrita física + commit no índice aconteceram,
# mas o `_journal()` correspondente falhou/nunca rodou (guard rejeitou
# path suspeito, ou processo morreu entre write e log). Simulamos
# gravando direto no filesystem + reindex (que não toca o journal),
# reproduzindo exatamente "arquivo indexado sem par no journal".

def test_write_then_log_orphan_is_adopted_and_journaled(tmp_path):
    home = _home(tmp_path)
    body = "Corpo consistente com o content_hash gravado no frontmatter.\n"
    entry_id = "01ORPHANWTL0000000000000"
    _lib_entry(home, "orphan.md", entry_id, "Orphan WTL", body)
    reindex(home)  # indexa, mas reindex não escreve journal — órfão puro

    before = [r for r in _journal(home) if r["verb"] != "reindex"]
    assert before == []  # nenhuma entrada de journal ainda

    report = curate_tick(home)

    assert report.reconciled == 1
    # A adoção do órfão write-then-log É uma catalogação: precisa contar
    # em `processed` (mesmo padrão dos outros pontos de catalogação do
    # arquivo), senão o snapshot commit não menciona a operação (spec §3).
    assert report.processed == 1
    assert report.errors == []
    assert (home.library / "orphan.md").is_file()  # nunca deleta

    journal = _journal(home)
    catalog_recs = [r for r in journal if r["verb"] == "catalog"
                    and r.get("dst") == "library/orphan.md"]
    assert len(catalog_recs) == 1
    assert catalog_recs[0]["item"] == entry_id
    assert catalog_recs[0].get("reconciled") == "write-then-log"

    con = connect(home)
    try:
        row = con.execute(
            "SELECT id, path FROM entries WHERE id=?", (entry_id,)).fetchone()
    finally:
        con.close()
    assert row is not None
    assert row[1] == "library/orphan.md"

    # report.processed > 0 -> commit_tick não deve cair no subject
    # fallback genérico ("snapshot: metadados atualizados"); a
    # catalogação da adoção precisa aparecer no snapshot commit.
    assert report.snapshot is not None
    snapshots = list_snapshots(home, limit=1)
    assert snapshots
    assert snapshots[0]["subject"] != "snapshot: metadados atualizados"
    assert "catalogados" in snapshots[0]["subject"]


def test_write_then_log_orphan_reconciliation_is_idempotent(tmp_path):
    home = _home(tmp_path)
    body = "Segundo corpo consistente pra testar idempotencia do tick.\n"
    entry_id = "01ORPHANIDEMPOT000000000"
    _lib_entry(home, "orphan2.md", entry_id, "Orphan Idempotent", body)
    reindex(home)

    first = curate_tick(home)
    assert first.reconciled == 1

    second = curate_tick(home)
    assert second.reconciled == 0
    assert second.quarantined == 0
    assert second.errors == []
    assert (home.library / "orphan2.md").is_file()

    journal = _journal(home)
    catalog_recs = [r for r in journal if r["verb"] == "catalog"
                    and r.get("dst") == "library/orphan2.md"]
    assert len(catalog_recs) == 1  # sem duplicar a reconciliação


def test_write_then_log_orphan_with_hash_mismatch_is_quarantined(tmp_path):
    # Frontmatter alega um content_hash que não bate com o corpo real em
    # disco (corrupção, ou edição direta fora do fluxo) — não é seguro
    # presumir a origem: quarentena, nunca deleta.
    home = _home(tmp_path)
    entry_id = "01ORPHANMISMATCH00000000"
    original_body = "Corpo original que gerou o content_hash indexado.\n"
    content_hash = hashlib.sha256(
        original_body.encode("utf-8")).hexdigest()
    tampered_body = "Corpo divergente, editado direto no disco por fora.\n"
    text = (f"---\nid: {entry_id}\ntitle: Orphan Mismatch\n"
           f"content_hash: {content_hash}\n---\n{tampered_body}")
    (home.library / "mismatch.md").write_text(text, encoding="utf-8")
    reindex(home)  # indexa com o content_hash (incorreto) do frontmatter

    report = curate_tick(home)

    assert report.reconciled == 0
    assert report.quarantined == 1
    assert len(report.errors) == 1
    assert not (home.library / "mismatch.md").exists()

    quarantined_files = list(home.quarantine.iterdir())
    assert len(quarantined_files) == 1
    assert quarantined_files[0].read_text(encoding="utf-8") == text

    journal = _journal(home)
    q_recs = [r for r in journal if r["verb"] == "quarantine"
             and r.get("item") == entry_id]
    assert len(q_recs) == 1
    assert "write-then-log" in q_recs[0]["reason"]

    con = connect(home)
    try:
        row = con.execute(
            "SELECT 1 FROM entries WHERE id=?", (entry_id,)).fetchone()
    finally:
        con.close()
    assert row is None  # entrada inconsistente removida do índice


def test_write_then_log_orphan_reconciliation_runs_before_new_catalog(
        tmp_path):
    # Um tick que tem tanto um órfão write-then-log quanto itens novos no
    # inbox reconcilia o órfão e cataloga o novo sem interferência mútua.
    home = _home(tmp_path)
    body = "Corpo do orfao coexistindo com item novo no mesmo tick.\n"
    entry_id = "01ORPHANCOEXIST00000000"
    _lib_entry(home, "orphan3.md", entry_id, "Orphan Coexist", body)
    reindex(home)
    _inbox(home, "novo.md",
          "# Nota Nova\n\nCorpo qualquer com texto suficiente aqui.\n")

    report = curate_tick(home)

    assert report.reconciled == 1
    # processed == 2: órfão reconciliado (T2) + item novo do inbox —
    # ambos são catalogação e contam em processed (T2 fix).
    assert report.processed == 2
    assert (home.library / "orphan3.md").is_file()
    assert list(home.inbox.glob("*.md")) == []


# ── v1.1: procedência curada ─────────────────────────────────────────

def test_tick_writes_provenance_for_curated_item(tmp_path):
    """Item comum passando pelo tick carrega o envelope pro índice."""
    home = _home(tmp_path)
    (home.inbox / "dep.md").write_text(
        "---\nid: 01TP\ntitle: Depositada\nsource:\n  agent: hermes\n"
        "  session: s-7\n  origin: manual\n---\nCorpo do grão.\n",
        encoding="utf-8")
    curate_tick(home)
    con = connect(home)
    row = con.execute(
        "SELECT agent, session, origin FROM entries WHERE id='01TP'"
    ).fetchone()
    assert tuple(row) == ("hermes", "s-7", "manual")


def test_tick_mirror_never_gets_provenance(tmp_path):
    """Invariante do regime: espelho é NULL nas três colunas MESMO com
    `source:` no arquivo — o `source:` de um arquivo espelhado é do autor
    externo, e atribuí-lo faria `agent:hermes` devolver material que
    hermes nunca depositou."""
    home = _home(tmp_path)
    body = "Corpo espelhado."
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    (home.inbox / "sk.md").write_text(
        "---\nid: 01TM\ntype: skill\nenv: claude-code\ntitle: Skill X\n"
        "description: d\nsource_key: claude-code:x\nsource_path: /tmp/x\n"
        "source:\n  agent: hermes\n  session: s-7\n  origin: manual\n"
        f"created: 2026-01-01T00:00:00+00:00\ncontent_hash: {content_hash}\n"
        f"---\n{body}", encoding="utf-8")
    curate_tick(home)
    con = connect(home)
    row = con.execute(
        "SELECT regime, agent, session, origin FROM entries WHERE id='01TM'"
    ).fetchone()
    assert tuple(row) == ("mirror", None, None, None)


def test_reindex_agrees_with_tick_on_provenance(tmp_path):
    """Índice é cache descartável: reconstruí-lo do zero tem de dar as
    mesmas três colunas que o caminho incremental do tick gravou."""
    home = _home(tmp_path)
    (home.inbox / "dep.md").write_text(
        "---\nid: 01TA\ntitle: A\nsource:\n  agent: hermes\n"
        "  session: s-7\n  origin: manual\n---\nCorpo A.\n",
        encoding="utf-8")
    curate_tick(home)
    con = connect(home)
    before = con.execute(
        "SELECT id, regime, agent, session, origin FROM entries"
        " ORDER BY id").fetchall()
    con.close()
    reindex(home)
    con = connect(home)
    after = con.execute(
        "SELECT id, regime, agent, session, origin FROM entries"
        " ORDER BY id").fetchall()
    assert before == after


# ── tick sobre schema antigo sem carimbo (v1.1, F3) ───────────────────
# Que `stamp_if_unversioned` não carimbe colunas velhas é unidade, e vive
# em test_indexdb.py; aqui interessa a porta: o tick nem chega a escrever.

def test_tick_refuses_old_unstamped_index_instead_of_crashing_midway(tmp_path):
    """Índice sem carimbo não é sinônimo de índice novo: pode ser um v7
    que nunca foi carimbado. Escrever nele estoura `OperationalError:
    table entries has no column named agent` no meio da curadoria, com
    parte do trabalho já feito. O tick recusa na entrada, com a instrução
    de cura."""
    home = _home(tmp_path)
    digest = _lib_entry(home, "motor-v7.md", "01V7", "Motor V7",
                        "vetores fundidos")
    con = connect(home)
    forge_v7_entries(con)
    insert_entry(con, "01V7", "motor-v7", "library/motor-v7.md", "Motor V7",
                 content_hash=digest)
    set_index_version(con, None)
    con.close()

    with pytest.raises(TickStructuralError, match="reindex"):
        curate_tick(home)


def test_query_heals_old_unstamped_index_by_reindexing(tmp_path):
    """A mesma forma velha pela porta da busca: `query` tem cura própria
    (`_ensure_searchable` reindexa quando há `.md` no disco), então ela
    responde em vez de recusar."""
    home = _home(tmp_path)
    digest = _lib_entry(home, "motor-v7.md", "01V7", "Motor V7",
                        "vetores fundidos")
    con = connect(home)
    forge_v7_entries(con)
    insert_entry(con, "01V7", "motor-v7", "library/motor-v7.md", "Motor V7",
                 content_hash=digest)
    # Texto no FTS: sem ele a busca não acha candidato e devolve lista
    # vazia sem chegar na projeção — passaria por ausência de dados, não
    # por cura.
    rowid = con.execute("SELECT rowid FROM entries WHERE id='01V7'"
                        ).fetchone()[0]
    fts_insert(con, rowid, "curated", title="Motor V7", aliases="",
               tags="", body="vetores fundidos")
    set_index_version(con, None)
    con.close()

    out = query(home, "vetores")
    assert [r["id"] for r in out["results"]] == ["01V7"]
