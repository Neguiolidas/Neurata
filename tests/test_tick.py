"""tests/test_tick.py — inbox → library, curadoria mecânica (spec v0.4 tick).

Cobre §1 (pipeline geral), §3 (renames/órfãos/entradas mortas) e §4
(near-dup). Ver docs/superpowers/specs/2026-07-18-neurata-v0.4-tick.md.
"""
import hashlib
import json
import os
import subprocess

import pytest
from conftest import forge_v7_entries, insert_entry, set_index_version

from neurata import archive
from neurata.frontmatter import parse, serialize
from neurata.grains import make_summary
from neurata.home import NeurataHome
from neurata.indexdb import connect, fts_insert
from neurata.query import query
from neurata.reindex import reindex
from neurata.snapshot import list_snapshots
from neurata.tick import (
    TickReport,
    TickStructuralError,
    _index_delete,
    _index_insert,
    _sync_update_in_place,
    curate_tick,
)
from neurata.tick import _journal as _write_journal
from neurata.usage import log_event


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
    original_meta, original_body = parse((home.library / "foo.md").read_text())
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
    # foo.md É um espelho pendente (regime=mirror, derived_from ausente):
    # o passo de compactação da v1.4 (§F3) o alcança na MESMA volta, depois
    # da reconciliação T2. `body` é parágrafo único sem heading — ponto
    # fixo de `make_summary` — então vira o caso "no-op sem ganho" do D-4:
    # o corpo servido não muda (ainda o mesmo texto), só `derived_from`/
    # `updated` entram no frontmatter. Por isso a comparação é sobre o
    # corpo (que É estável, o que o nome do teste afirma), não sobre o
    # texto bruto do arquivo (que ganha um campo novo, esperado).
    new_meta, new_body = parse((home.library / "foo.md").read_text())
    assert new_body == original_body
    assert new_meta["id"] == original_meta["id"]
    assert new_meta["title"] == original_meta["title"]
    assert new_meta.get("derived_from")  # F3: no-op ainda ganha a marca
    journal = _journal(home)
    catalog_recs = [r for r in journal if r["verb"] == "catalog"]
    assert len(catalog_recs) == 1
    assert catalog_recs[0]["item"] == "01SKILLLIB000000000000"
    assert catalog_recs[0].get("reconciled") == "write-then-log"
    # sem "update" pro skill-item: ele é mesmo um no-op, o compact do F3
    # não é update — é o único outro verb que pode aparecer aqui.
    assert {r["verb"] for r in journal} <= {"catalog", "compact"}


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


def test_write_then_log_orphan_with_edited_body_is_absorbed(tmp_path):
    # v1.2 (D7): corpo divergente do content_hash indexado é EDIÇÃO, não
    # corrupção — o arquivo é a verdade. A absorção roda antes do passo
    # de órfãos e sincroniza o hash, então a decisão de (3) recai só
    # sobre a identidade, que aqui bate: adota em vez de quarentenar.
    # Antes da v1.2 este mesmo cenário perdia a edição na quarentena.
    home = _home(tmp_path)
    entry_id = "01ORPHANMISMATCH00000000"
    original_body = "Corpo original que gerou o content_hash indexado.\n"
    content_hash = hashlib.sha256(
        original_body.encode("utf-8")).hexdigest()
    edited_body = "Corpo divergente, editado direto no disco por fora.\n"
    text = (f"---\nid: {entry_id}\ntitle: Orphan Mismatch\n"
           f"content_hash: {content_hash}\n---\n{edited_body}")
    (home.library / "mismatch.md").write_text(text, encoding="utf-8")
    reindex(home)  # indexa com o content_hash (obsoleto) do frontmatter

    report = curate_tick(home)

    assert report.absorbed == 1
    assert report.reconciled == 1
    assert report.quarantined == 0
    assert report.errors == []
    assert (home.library / "mismatch.md").is_file()
    assert list(home.quarantine.iterdir()) == []

    con = connect(home)
    try:
        row = con.execute(
            "SELECT content_hash FROM entries WHERE id=?",
            (entry_id,)).fetchone()
    finally:
        con.close()
    assert row[0] == hashlib.sha256(
        edited_body.encode("utf-8")).hexdigest()


def test_write_then_log_orphan_with_id_mismatch_is_quarantined(tmp_path):
    # O que a absorção NÃO cobre e segue valendo quarentena: o id do
    # arquivo não é o id indexado. Não é edição de um grão, é outro grão
    # ocupando o lugar — presumir origem aqui seria adotar um estranho.
    home = _home(tmp_path)
    entry_id = "01ORPHANIDMISMATCH000000"
    text = (f"---\nid: {entry_id}\ntitle: Orphan Mismatch\n---\n"
           "Corpo original.\n")
    (home.library / "mismatch.md").write_text(text, encoding="utf-8")
    reindex(home)  # índice guarda entry_id

    trocado = text.replace(entry_id, "01OUTROGRAOOCUPANDOOLUGAR")
    (home.library / "mismatch.md").write_text(trocado, encoding="utf-8")

    report = curate_tick(home)

    assert report.absorbed == 0
    assert report.reconciled == 0
    assert report.quarantined == 1
    assert len(report.errors) == 1
    assert not (home.library / "mismatch.md").exists()

    quarantined_files = list(home.quarantine.iterdir())
    assert len(quarantined_files) == 1
    assert quarantined_files[0].read_text(encoding="utf-8") == trocado

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


# ── v1.4 F1: derived_hash/source_path/derived_from pelo tick ──────────

def test_tick_writes_derived_hash(tmp_path):
    """v1.4 F1: o caminho incremental do tick também preenche
    `derived_hash` (antes coluna morta) — hash do corpo servido, igual
    ao que `reindex` grava para o mesmo arquivo."""
    home = _home(tmp_path)
    body = "Corpo do grao.\n"
    (home.inbox / "dep.md").write_text(
        f"---\nid: 01TH\ntitle: Depositada\n---\n{body}", encoding="utf-8")
    curate_tick(home)
    con = connect(home)
    row = con.execute(
        "SELECT derived_hash FROM entries WHERE id='01TH'").fetchone()
    assert row[0] == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_tick_mirror_carries_source_path_and_derived_from(tmp_path):
    """Espelho catalogado pelo tick leva `source_path`/`derived_from` do
    frontmatter pro índice — mortas desde a v10, viram coluna consultável
    na v11."""
    home = _home(tmp_path)
    body = "Corpo espelhado."
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    derived_from = "f" * 64
    (home.inbox / "sk.md").write_text(
        "---\nid: 01TS\ntype: skill\nenv: claude-code\ntitle: Skill X\n"
        "description: d\nsource_key: claude-code:x\nsource_path: /tmp/x\n"
        f"derived_from: {derived_from}\n"
        f"created: 2026-01-01T00:00:00+00:00\ncontent_hash: {content_hash}\n"
        f"---\n{body}", encoding="utf-8")
    curate_tick(home)
    con = connect(home)
    row = con.execute(
        "SELECT source_path, derived_from FROM entries WHERE id='01TS'"
    ).fetchone()
    assert tuple(row) == ("/tmp/x", derived_from)


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


def test_tick_survives_list_project_in_frontmatter(tmp_path):
    """`project: [a, b]` hoje derruba o tick inteiro com ProgrammingError
    ("Error binding parameter 9: type 'list'"). Passa a virar NULL — um
    grão mal escrito não pode matar a curadoria dos outros.

    A lista tem que ser inline: o parser restrito rejeita bloco `- a` como
    linha inválida, e aí o grão nem chega ao INSERT (testaria nada).
    """
    home = _home(tmp_path)
    _inbox(home, "a.md", "---\ntitle: Grão\nproject: [a, b]\n---\ncorpo\n")
    report = curate_tick(home)
    assert report.processed == 1
    assert report.errors == []
    con = connect(home)
    try:
        assert con.execute("SELECT project FROM entries").fetchone()[0] is None
    finally:
        con.close()


def test_deposit_inside_agent_indexes_provenance_and_project(tmp_path,
                                                             monkeypatch):
    """Critérios 1 e 4: o depósito feito de dentro de um agente grava
    `agent`/`session` sem flag nenhuma, o `project` sai do repo, e as
    facets da v1.1 passam a ter dado — que era o ponto da versão."""
    from neurata.deposit import deposit

    repo = tmp_path / "Neurata"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

    monkeypatch.delenv("NEURATA_AGENT", raising=False)
    monkeypatch.delenv("NEURATA_SESSION", raising=False)
    monkeypatch.setenv("AI_AGENT", "claude-code_2-1-229_agent")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-ponta-a-ponta")
    monkeypatch.chdir(repo)

    home = _home(tmp_path / "acervo")
    deposit(home, title="Grão xenoglossia", content="corpo do grão")
    curate_tick(home)

    con = connect(home)
    try:
        assert con.execute(
            "SELECT agent, session, project FROM entries").fetchone() == (
                "claude-code", "s-ponta-a-ponta", "Neurata")
    finally:
        con.close()

    assert len(query(home, "project:Neurata")["results"]) == 1
    assert len(query(home, "session:s-ponta-a-ponta")["results"]) == 1
    assert len(query(home, "agent:claude-code")["results"]) == 1
    assert query(home, "missing:agent")["results"] == []


def test_indexed_inbox_item_does_not_conflict_with_itself(tmp_path):
    """Grão já indexado no inbox (após reindex) não conflita consigo mesmo.

    Regressão: o laço de near-dup varria shingle_sets inteiro, incluindo a
    própria entrada do item quando um reindex anterior já a havia indexado
    (location='inbox' escapa do dedup exato, que só olha 'library').
    Resultado: jaccard 1.0 contra si mesmo => conflicts_with: [próprio id].
    """
    home = _home(tmp_path)
    item_id = "01SELFCONFLICT0000000000"
    body = " ".join(f"palavra{i}" for i in range(30)) + ".\n"
    _inbox(home, "solo.md",
          f"---\nid: {item_id}\ntitle: Solo\n---\n{body}")
    reindex(home)

    report = curate_tick(home)

    assert report.conflicts == 0
    dest = next(home.library.glob("solo*.md"))
    meta, _ = parse(dest.read_text())
    assert "conflicts_with" not in meta


def test_preexisting_self_conflict_is_healed(tmp_path):
    """Auto-conflito gravado por versão antiga é removido no próximo tick."""
    home = _home(tmp_path)
    item_id = "01SELFHEAL00000000000000"
    body = " ".join(f"palavra{i}" for i in range(30)) + ".\n"
    _inbox(home, "curar.md",
          f"---\nid: {item_id}\ntitle: Curar\n"
          f"conflicts_with: [{item_id}]\n---\n{body}")

    curate_tick(home)

    dest = next(home.library.glob("curar*.md"))
    meta, _ = parse(dest.read_text())
    assert "conflicts_with" not in meta


# ── v1.4 §F3: compactação de espelho sob o tick ─────────────────────
# Cobre o design v1.4 (docs/superpowers/specs/2026-08-14-neurata-v1.4-
# ciclo-de-vida-design.md): A11 (crash nos 5 pontos da ordem §4
# converge), A14 (teto exato de 500/tick), a drenagem do no-op (D-4,
# medido: 4,0% da amostra real não tem ganho) e a idempotência.
#
# `_FULL_BODY` tem 3 parágrafos sem heading: `make_summary` (sem
# heading) usa só os 2 primeiros — ganho real garantido, diferente dos
# corpos de parágrafo único usados no resto do arquivo (que são ponto
# fixo/no-op).

_FULL_BODY = (
    "Primeiro paragrafo do corpo completo do espelho de teste, com "
    "texto o bastante pra nao ficar vazio aqui mesmo.\n\n"
    "Segundo paragrafo com mais informacao relevante que tambem "
    "sobrevive no resumo mecanico gerado pelo make_summary.\n\n"
    "Terceiro paragrafo que so existe no corpo completo — nunca "
    "aparece no resumo, e é por isso que a compactação tem ganho "
    "real aqui.\n"
)


def _seed_mirror(home, filename, entry_id, source_key, title, body):
    """Cria um espelho direto na library (como `_lib_skill_entry`), mas
    também seeda um registro `catalog` no journal com `dst` = seu path
    — pra `_reconcile_journal_orphans` (T2) não o tratar como órfão
    write-then-log. Sem isto, os testes de crash de compactação abaixo
    ficariam acoplados à reconciliação de órfãos (preocupação
    ortogonal): qualquer mutação manual do arquivo pós-`reindex`, feita
    pra simular um estado de crash, divergiria de `derived_hash` no
    índice e seria (corretamente, por outro motivo) quarentenada antes
    de `_compact_mirrors` sequer rodar."""
    _lib_skill_entry(home, filename, entry_id, source_key, title, body)
    rel = f"library/{filename}"
    seed_report = TickReport(tick="01SEEDJOURNAL0000000000000")
    _write_journal(home, "01SEEDJOURNAL0000000000000", "catalog", entry_id,
                  None, rel, seed_report,
                  content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest())
    reindex(home)


def test_compact_crash_after_archive_put_recovers(tmp_path):
    """Ponto 1 do §4: crash logo após `archive.put`, antes da escrita do
    arquivo. Estado deixado: blob no archive, arquivo e índice intocados
    (ainda com o corpo cheio original). Reconserto: `archive.put` é
    content-addressed com dedup — a volta seguinte chama de novo com o
    mesmo corpo e recebe o MESMO sha, sem blob duplicado nem corrupção
    (spec §4, linha "archive.put" → "inócuo")."""
    home = _home(tmp_path)
    entry_id = "01MIRRORPT1000000000000"
    _seed_mirror(home, "m1.md", entry_id, "claude-code:pt1", "M1",
                _FULL_BODY)

    pre_sha = archive.put(home, _FULL_BODY.encode("utf-8"))

    report = curate_tick(home)

    assert report.compacted == 1
    assert report.errors == []
    meta, body = parse((home.library / "m1.md").read_text())
    assert body == make_summary(_FULL_BODY)
    assert meta["derived_from"] == pre_sha
    assert archive.get(home, meta["derived_from"]).decode("utf-8") == \
        _FULL_BODY
    con = connect(home)
    row = con.execute(
        "SELECT derived_from FROM entries WHERE id=?", (entry_id,)
    ).fetchone()
    assert row[0] == pre_sha


def test_compact_crash_after_file_write_preserves_derived_from_on_retry(
        tmp_path):
    """Ponto 2 do §4 — o CRÍTICO: crash após a escrita do arquivo
    (archive + `derived_from` corretos em disco), antes do índice.

    Uma implementação ingênua que decidisse pelo `action` devolvido por
    `compact()` (em vez de reler o que já está no arquivo) trataria o
    "noop" que `compact()` devolve na retomada — `make_summary` do
    summary já escrito é ponto fixo — como "sem ganho, do zero", re-
    arquivando o SUMMARY como se fosse o corpo cheio e sobrescrevendo o
    `derived_from` correto por um hash órfão. `_finish_mirror` evita
    isso relendo o frontmatter e só tratando como "novo" o que ainda não
    tem `derived_from`."""
    home = _home(tmp_path)
    entry_id = "01MIRRORPT2000000000000"
    _seed_mirror(home, "m2.md", entry_id, "claude-code:pt2", "M2",
                _FULL_BODY)
    # índice: derived_from NULL, corpo == _FULL_BODY

    true_sha = archive.put(home, _FULL_BODY.encode("utf-8"))
    summary = make_summary(_FULL_BODY)
    lib_path = home.library / "m2.md"
    meta, _ = parse(lib_path.read_text())
    meta = dict(meta)
    meta["derived_from"] = true_sha
    lib_path.write_text(serialize(meta, summary), encoding="utf-8")
    # índice DELIBERADAMENTE não tocado — é exatamente o que um crash
    # entre a escrita do arquivo e o índice deixa observável de fora.

    report = curate_tick(home)

    assert report.compacted == 1
    assert report.errors == []
    new_meta, new_body = parse(lib_path.read_text())
    assert new_body == summary  # não regrediu, não recompactou do zero
    assert new_meta["derived_from"] == true_sha  # a asserção crítica
    assert archive.get(home, true_sha).decode("utf-8") == _FULL_BODY
    con = connect(home)
    row = con.execute(
        "SELECT derived_from, derived_hash FROM entries WHERE id=?",
        (entry_id,)).fetchone()
    assert row[0] == true_sha
    assert row[1] == hashlib.sha256(summary.encode("utf-8")).hexdigest()


def test_index_write_without_commit_is_invisible_after_reconnect(tmp_path):
    """Ponto 3 do §4: crash entre a escrita do índice (passo 3) e o
    commit (passo 4). Não são dois estados observáveis diferentes: uma
    escrita SQLite só fica visível após commit — uma conexão que morre
    sem commitar não deixa rastro pra quem reconecta depois, então o
    ponto 3 colapsa no mesmo estado recuperável do ponto 2 (índice
    intocado). Este teste prova a premissa de atomicidade que sustenta
    essa afirmação, direto contra o driver, sem depender de monkeypatch
    frágil em `curate_tick`."""
    home = _home(tmp_path)
    body = "Corpo generico com id conhecido pro teste de atomicidade.\n"
    entry_id = "01ATOMICTEST00000000000"
    _lib_entry(home, "atomic.md", entry_id, "Atomic", body)
    reindex(home)

    con = connect(home)
    con.execute("UPDATE entries SET derived_from=? WHERE id=?",
               ("f" * 64, entry_id))
    con.close()  # nunca commitou — a mudança some com a conexão

    con2 = connect(home)
    row = con2.execute(
        "SELECT derived_from FROM entries WHERE id=?", (entry_id,)
    ).fetchone()
    assert row[0] is None


def test_compact_crash_after_commit_before_journal_never_reprocesses(
        tmp_path):
    """Ponto 4 do §4: crash após o commit do índice, antes do journal —
    arquivo, archive e índice já consistentes; só a linha "compact" no
    journal nunca saiu (§4, linha "commit" → "`_reconcile_journal_
    orphans` re-loga").

    A releitura é via `catalog`/reconciled=write-then-log, não via
    `compact`: o path já tem uma explicação de chegada na library
    anterior a esta compactação (`_CATALOG_VERBS` não inclui "compact"
    de propósito — ver docstring de `_compact_mirrors` em tick.py), e o
    índice já mostra `derived_from` preenchido, então o predicado de
    pendência (D-4) exclui o grão: a volta seguinte não reprocessa nem
    sobrescreve o hash correto. A única perda é a linha informativa
    "compact" em si — não a consistência arquivo+índice, que é o que a
    convergência promete."""
    home = _home(tmp_path)
    entry_id = "01MIRRORPT4000000000000"
    _seed_mirror(home, "m4.md", entry_id, "claude-code:pt4", "M4",
                _FULL_BODY)
    lib_path = home.library / "m4.md"

    sha = archive.put(home, _FULL_BODY.encode("utf-8"))
    summary = make_summary(_FULL_BODY)
    meta, _ = parse(lib_path.read_text())
    meta = dict(meta)
    meta["derived_from"] = sha
    lib_path.write_text(serialize(meta, summary), encoding="utf-8")
    con = connect(home)
    _index_delete(con, entry_id)
    _index_insert(con, meta, summary, "library/m4.md", "library", "m4")
    con.commit()
    con.close()
    # journal deliberadamente vazio pra este grão — reproduz o "sem
    # journal" da tabela §4.

    report = curate_tick(home)

    assert report.compacted == 0  # já tinha derived_from: fora do predicado
    assert report.errors == []
    final_meta, final_body = parse(lib_path.read_text())
    assert final_meta["derived_from"] == sha
    assert final_body == summary
    assert not any(r.get("item") == entry_id and r["verb"] == "compact"
                  for r in _journal(home))


def test_second_tick_does_not_recompact_already_compacted_mirror(tmp_path):
    """Ponto 5 do §4 (sem crash — sucesso completo) + idempotência: uma
    segunda volta sobre um espelho já compactado não recompacta, não
    reescreve arquivo/índice e não duplica journal."""
    home = _home(tmp_path)
    entry_id = "01MIRRORIDEMP00000000000"
    _lib_skill_entry(home, "idemp.md", entry_id, "claude-code:idemp",
                     "Idemp", _FULL_BODY)
    reindex(home)

    report1 = curate_tick(home)
    assert report1.compacted == 1
    lib_path = home.library / "idemp.md"
    meta1, body1 = parse(lib_path.read_text())
    journal1 = _journal(home)

    report2 = curate_tick(home)

    assert report2.compacted == 0
    meta2, body2 = parse(lib_path.read_text())
    assert meta2 == meta1
    assert body2 == body1
    assert _journal(home) == journal1  # nada novo gravado


def test_noop_mirror_drains_pending_predicate_and_stays_drained(tmp_path):
    """O fato medido nesta versão: 4,0% dos espelhos reais não têm ganho
    (o summary não encolhe o corpo) — `compact()` devolve `action=noop` e NÃO
    escreve `derived_from` sozinho. Sem o passo do tick persistir a
    marca mesmo aqui, esses grãos nunca sairiam do predicado de
    pendência e encheriam o teto de 500 pra sempre (D-4). Este teste
    prova especificamente isso: um espelho cujo corpo já é seu próprio
    resumo sai do predicado depois de um tick e NÃO reaparece no
    seguinte."""
    home = _home(tmp_path)
    body = "Corpo de paragrafo unico que ja e o proprio resumo mecanico.\n"
    entry_id = "01MIRRORNOOP0000000000000"
    _lib_skill_entry(home, "noop.md", entry_id, "claude-code:noop", "Noop",
                     body)
    reindex(home)
    con = connect(home)
    pending = con.execute(
        "SELECT 1 FROM entries WHERE id=? AND regime='mirror' AND"
        " derived_from IS NULL", (entry_id,)).fetchone()
    assert pending is not None  # começa pendente

    report1 = curate_tick(home)

    assert report1.compacted == 1
    con = connect(home)
    row = con.execute(
        "SELECT derived_from FROM entries WHERE id=?", (entry_id,)
    ).fetchone()
    assert row[0] is not None
    meta, body_after = parse((home.library / "noop.md").read_text())
    assert body_after == body  # sem ganho: corpo servido não muda
    assert meta["derived_from"] == row[0]

    report2 = curate_tick(home)

    assert report2.compacted == 0
    con = connect(home)
    still_pending = con.execute(
        "SELECT 1 FROM entries WHERE id=? AND regime='mirror' AND"
        " derived_from IS NULL", (entry_id,)).fetchone()
    assert still_pending is None


def test_compact_mirrors_respects_cap_of_500_per_tick(tmp_path):
    """A14: com N > 500 espelhos pendentes, uma volta compacta exatamente
    500 — o teto do D-4 (retomada é a própria consulta, sem cursor)."""
    home = _home(tmp_path)
    n = 505
    for i in range(n):
        body = (f"Corpo do espelho numero {i} com texto o bastante "
                f"pra nao ficar vazio no teste de teto.\n")
        _lib_skill_entry(home, f"m{i:04d}.md", f"01MIRRORCAP{i:014d}",
                         f"claude-code:cap{i}", f"Cap {i}", body)
    reindex(home)

    report1 = curate_tick(home)

    assert report1.compacted == 500
    con = connect(home)
    remaining = con.execute(
        "SELECT COUNT(*) FROM entries WHERE regime='mirror' AND"
        " derived_from IS NULL").fetchone()[0]
    assert remaining == n - 500

    report2 = curate_tick(home)

    assert report2.compacted == n - 500
    con = connect(home)
    remaining2 = con.execute(
        "SELECT COUNT(*) FROM entries WHERE regime='mirror' AND"
        " derived_from IS NULL").fetchone()[0]
    assert remaining2 == 0


def test_reconcile_journal_orphans_checks_derived_hash_not_content_hash(
        tmp_path):
    """Regressão do design v1.4 §2/D-1: `_reconcile_journal_orphans`
    passou a conferir `derived_hash` (corpo SERVIDO), não `content_hash`
    (corpo da FONTE) — os dois DIVERGEM por construção pra um espelho já
    compactado. Simula um grão compactado numa sessão anterior (arquivo
    já é o summary + `derived_from`) cujo `content_hash` indexado
    deliberadamente não bate com o corpo em disco (como seria o caso
    real: `content_hash` é o hash do corpo CHEIO, nunca tocado pela
    compactação), mas cujo `derived_hash` bate. Antes do fix, isso
    quarentenaria todo espelho compactado que passasse por aqui sem
    nunca ter sido logado; depois do fix, reconcilia normal."""
    home = _home(tmp_path)
    summary = make_summary(_FULL_BODY)
    entry_id = "01MIRRORDHASH000000000000"
    sha = archive.put(home, _FULL_BODY.encode("utf-8"))
    _lib_skill_entry(home, "dhash.md", entry_id, "claude-code:dhash",
                     "DHash", summary)
    lib_path = home.library / "dhash.md"
    meta, _ = parse(lib_path.read_text())
    meta = dict(meta)
    meta["derived_from"] = sha
    lib_path.write_text(serialize(meta, summary), encoding="utf-8")
    reindex(home)  # derived_hash = sha256(summary) — correto

    con = connect(home)
    content_hash_source = hashlib.sha256(
        _FULL_BODY.encode("utf-8")).hexdigest()
    con.execute("UPDATE entries SET content_hash=? WHERE id=?",
               (content_hash_source, entry_id))
    con.commit()

    report = curate_tick(home)

    assert report.reconciled == 1
    assert report.quarantined == 0
    assert list(home.quarantine.glob("*.md")) == []
    journal = _journal(home)
    rec = next(r for r in journal if r["item"] == entry_id and
              r["verb"] == "catalog")
    assert rec.get("reconciled") == "write-then-log"


def test_sync_update_in_place_clears_stale_derived_from_and_recompacts(
        tmp_path):
    """Regressão: `_sync_update_in_place` limpa `derived_from` velho ao
    absorver um corpo novo via skill-item — sem isso o arquivo ficaria
    com corpo novo cru + marca de derivação do corpo ANTIGO
    (`expand --restore` restauraria o full errado). `_compact_mirrors`,
    mais adiante NO MESMO tick, recompacta o corpo novo do zero e
    escreve um `derived_from` correto, diferente do velho."""
    home = _home(tmp_path)
    entry_id = "01MIRRORSYNCFIX000000000"
    _lib_skill_entry(home, "sync.md", entry_id, "claude-code:sync", "Sync",
                     _FULL_BODY)
    reindex(home)
    r1 = curate_tick(home)
    assert r1.compacted == 1
    meta1, body1 = parse((home.library / "sync.md").read_text())
    old_derived_from = meta1["derived_from"]
    assert old_derived_from
    assert body1 == make_summary(_FULL_BODY)

    new_full_body = (
        "Paragrafo novo numero um do corpo atualizado pela skill de "
        "novo, bem diferente do anterior.\n\n"
        "Paragrafo novo numero dois que tambem sobrevive no resumo "
        "novo gerado a partir deste corpo atualizado.\n\n"
        "Paragrafo novo numero tres que so existe no corpo completo "
        "novo, garantindo ganho real de novo na recompactação.\n"
    )
    _skill_item(home, "sync-new.md", "01SYNCINBOXNEW000000000",
               "claude-code:sync", "Sync", new_full_body)

    report2 = curate_tick(home)

    assert report2.updated == 1
    assert report2.compacted == 1  # F3 recompactou o corpo novo na mesma volta
    meta2, body2 = parse((home.library / "sync.md").read_text())
    assert body2 == make_summary(new_full_body)
    assert body2 != body1
    new_derived_from = meta2.get("derived_from")
    assert new_derived_from
    assert new_derived_from != old_derived_from  # sem marca velha sobrando
    assert archive.get(home, new_derived_from).decode("utf-8") == \
        new_full_body
    assert archive.get(home, old_derived_from).decode("utf-8") == _FULL_BODY


def test_compact_failure_on_one_mirror_does_not_crash_tick_or_others(
        tmp_path, monkeypatch):
    """Um espelho cujo `compact()` explode não derruba o tick nem os
    demais grãos: fica registrado em `report.errors`, segue pendente
    (nada foi escrito — `_finish_mirror` nunca roda pra ele) e o journal
    não ganha linha nenhuma pra ele. O grão saudável no mesmo tick
    compacta normalmente. Numa volta seguinte sem a falha, o pendente
    que sobrou compacta também — nada corrompeu."""
    home = _home(tmp_path)
    body_bad = "Corpo do espelho que vai falhar ao compactar de proposito.\n"
    body_ok = "Corpo do espelho que compacta normal sem problema nenhum.\n"
    bad_id = "01MIRRORBAD000000000000"
    ok_id = "01MIRRORFINE00000000000"
    _lib_skill_entry(home, "bad.md", bad_id, "claude-code:bad", "Bad",
                     body_bad)
    _lib_skill_entry(home, "ok.md", ok_id, "claude-code:ok", "Ok", body_ok)
    reindex(home)

    real_compact = __import__("neurata.compact", fromlist=["compact"]).compact

    def flaky_compact(home_arg, ref, reindex_after=True, path=None):
        if ref == bad_id:
            raise RuntimeError("falha simulada de compactação")
        return real_compact(home_arg, ref, reindex_after=reindex_after,
                            path=path)

    monkeypatch.setattr("neurata.tick.compact", flaky_compact)

    report = curate_tick(home)

    assert report.compacted == 1  # só o ok.md
    assert any("falha ao compactar" in e.reason for e in report.errors)
    con = connect(home)
    row_bad = con.execute(
        "SELECT derived_from FROM entries WHERE id=?", (bad_id,)).fetchone()
    assert row_bad[0] is None  # continua pendente, nada foi escrito
    row_ok = con.execute(
        "SELECT derived_from FROM entries WHERE id=?", (ok_id,)).fetchone()
    assert row_ok[0] is not None
    journal = _journal(home)
    assert not any(r.get("item") == bad_id and r["verb"] == "compact"
                  for r in journal)

    monkeypatch.undo()
    report2 = curate_tick(home)

    assert report2.compacted == 1  # o pendente que sobrou compacta normal
    con = connect(home)
    row_bad2 = con.execute(
        "SELECT derived_from FROM entries WHERE id=?", (bad_id,)).fetchone()
    assert row_bad2[0] is not None


# ── v1.4 §F3: guard de frieza (espelho que a busca usa não é compactado) ──


def _mirror(home, n, body=None):
    """Espelho pendente pronto pra compactar. Devolve o entry_id."""
    eid = f"01MIRRORHOT{n:014d}"
    _lib_skill_entry(home, f"hot{n}.md", eid, f"claude-code:hot{n}",
                     f"Hot {n}", body or (
                         "## Secao\n\nPrimeiro paragrafo que sobrevive ao "
                         "summary.\n\nSegundo paragrafo, esse a compactacao "
                         "descarta e por isso o corpo encolhe.\n"))
    return eid


def test_compact_skips_mirror_the_search_uses(tmp_path):
    """§F3: espelho com >= 2 usos registrados não é compactado — medido
    em 2026-08-14: compactar derruba do top-10 grão que a busca usava."""
    home = _home(tmp_path)
    eid = _mirror(home, 1)
    reindex(home)
    log_event(home, "query", eid, query="algo", rank=1)
    log_event(home, "query", eid, query="outra", rank=3)

    report = curate_tick(home)

    assert report.compacted == 0
    con = connect(home)
    derived = con.execute("SELECT derived_from FROM entries WHERE id=?",
                         (eid,)).fetchone()[0]
    assert derived is None


def test_compact_takes_mirror_below_hit_threshold(tmp_path):
    """Um acesso só não protege: o limiar é 2, não 1 (senão a primeira
    impressão de qualquer grão congelaria o acervo inteiro)."""
    home = _home(tmp_path)
    eid = _mirror(home, 2)
    reindex(home)
    log_event(home, "query", eid, query="algo", rank=9)

    report = curate_tick(home)

    assert report.compacted == 1


def test_expand_counts_toward_hit_threshold(tmp_path):
    """Abrir o grão (expand) é sinal de uso mais forte que aparecer numa
    lista (query); os dois somam pro limiar."""
    home = _home(tmp_path)
    eid = _mirror(home, 3)
    reindex(home)
    log_event(home, "query", eid, query="algo", rank=1)
    log_event(home, "expand", eid)

    assert curate_tick(home).compacted == 0


def test_hot_mirror_does_not_burn_cap_slot(tmp_path, monkeypatch):
    """O filtro entra antes do LIMIT: com teto de 1, o espelho quente não
    pode consumir a vaga e deixar o frio pra próxima volta — senão a
    compactação anda mais devagar a cada volta, queimando as mesmas vagas
    nos mesmos grãos."""
    home = _home(tmp_path)
    quente = _mirror(home, 4)
    frio = _mirror(home, 5)
    reindex(home)
    log_event(home, "query", quente, query="algo", rank=1)
    log_event(home, "query", quente, query="outra", rank=2)
    monkeypatch.setattr("neurata.tick._COMPACT_LIMIT", 1)

    report = curate_tick(home)

    assert report.compacted == 1
    con = connect(home)
    assert con.execute("SELECT derived_from FROM entries WHERE id=?",
                      (frio,)).fetchone()[0] is not None
    assert con.execute("SELECT derived_from FROM entries WHERE id=?",
                      (quente,)).fetchone()[0] is None


def test_null_entry_id_in_usage_log_does_not_disable_compaction(tmp_path):
    """Regressão: `id NOT IN (<lista com NULL>)` é NULL pra toda linha em
    SQL de três valores. Uma linha `entry_id: null` no log zeraria os
    candidatos e desligaria a compactação em silêncio, pra sempre."""
    home = _home(tmp_path)
    _mirror(home, 6)
    reindex(home)
    with (home.logs / "usage.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": "2026-08-14T00:00:00+00:00",
                             "event": "query", "entry_id": None,
                             "query": "x", "rank": 1}) + "\n")

    assert curate_tick(home).compacted == 1


def test_compaction_skipped_when_usage_log_unreadable(tmp_path,
                                                      monkeypatch):
    """Falha fechada: sem saber o que a busca usa, compactar é apostar
    contra o recall. Pular só adia disco; compactar às cegas rebaixa grão
    quente e só volta atrás por expand manual."""
    home = _home(tmp_path)
    _mirror(home, 7)
    reindex(home)

    def explode(_home):
        raise OSError("disco de log ilegivel")

    monkeypatch.setattr("neurata.tick.read_usage", explode)

    report = curate_tick(home)

    assert report.compacted == 0
    assert any("uso ilegível" in e.reason for e in report.errors)
