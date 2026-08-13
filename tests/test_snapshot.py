"""tests/test_snapshot.py"""
import os
import subprocess

import pytest

from neurata import snapshot
from neurata.home import CONTRACT_VERSION, SCHEMA_VERSION, NeurataHome
from neurata.indexdb import INDEX_SCHEMA_VERSION
from neurata.query import query
from neurata.reindex import reindex
from neurata.snapshot import (
    SnapshotError,
    _tick_body,
    _tick_subject,
    commit,
    commit_manual,
    commit_tick,
    ensure_repo,
    git_available,
    has_changes,
    list_snapshots,
    push,
    restore,
    restore_dry_run,
    set_remote,
)
from neurata.tick import TickReport


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


@pytest.fixture(autouse=True)
def _isolated_git_env(tmp_path_factory, monkeypatch):
    """Nunca deixa config global/system do host vazar para os testes."""
    empty_global = tmp_path_factory.mktemp("gitconfig") / "gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_global))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setattr(snapshot, "_AVAIL", None)


def test_git_available_true():
    assert git_available() is True


def test_ensure_repo_creates_git_once_and_is_idempotent(tmp_path, monkeypatch):
    home = _home(tmp_path)
    assert ensure_repo(home) is True
    assert (home.library / ".git").is_dir()

    calls = []
    orig_run = subprocess.run

    def _spy(*args, **kwargs):
        calls.append(args)
        return orig_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy)
    assert ensure_repo(home) is True
    assert calls == []


def test_ensure_repo_returns_false_when_init_fails(tmp_path, monkeypatch):
    # git disponível, mas init vira no-op → .git nunca criado → não mentir.
    home = _home(tmp_path)
    monkeypatch.setattr(
        snapshot, "_run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, "", "boom"))
    assert ensure_repo(home) is False


def test_ensure_repo_local_identity_isolated(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    name = subprocess.run(
        ["git", "-C", str(home.library), "config", "user.name"],
        capture_output=True, text=True, check=True).stdout.strip()
    gpgsign = subprocess.run(
        ["git", "-C", str(home.library), "config", "commit.gpgsign"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert name == "neurata"
    assert gpgsign == "false"


def test_no_git_everything_returns_falsy_without_raising(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.setattr(snapshot, "_AVAIL", None)
    assert git_available() is False
    assert ensure_repo(home) is False
    assert has_changes(home) is False
    assert commit(home, "subject") is None


def test_commit_skips_when_tree_clean(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    assert commit(home, "no changes yet") is None


def test_commit_returns_sha_prefix(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    sha = commit(home, "feat: primeira nota")
    assert sha is not None
    assert len(sha) >= 12
    assert all(c in "0123456789abcdef" for c in sha)


def test_commit_does_not_inherit_global_identity(tmp_path, tmp_path_factory, monkeypatch):
    global_cfg = tmp_path_factory.mktemp("hostcfg") / "gitconfig"
    global_cfg.write_text("[user]\n\tname = Host User\n\temail = host@example.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_cfg))

    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    commit(home, "feat: identidade isolada")

    author = subprocess.run(
        ["git", "-C", str(home.library), "log", "-1", "--pretty=%an <%ae>"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert author == "neurata <neurata@localhost>"


def test_set_remote_add_then_update(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    set_remote(home, "https://example.invalid/a.git")
    set_remote(home, "https://example.invalid/b.git")
    url = subprocess.run(
        ["git", "-C", str(home.library), "remote", "get-url", "neurata"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert url == "https://example.invalid/b.git"


# ── _tick_subject / _tick_body / commit_tick (spec §3+§4) ────────────
#
# `TickReport` real (não um fake) porque a regra de negócio ("duck-typing
# no snapshot.py") é sobre o *módulo* snapshot.py não importar tick.py —
# não impede o teste (leaf, sem risco de ciclo) de exercitar a integração
# real dos dois tipos.

def test_tick_subject_lists_nonzero_categories_in_fixed_order():
    report = TickReport(tick="01JTICK0000000000000000000", processed=3,
                        literate=2, updated=1, quarantined=2)
    assert (_tick_subject(report) ==
           "snapshot: +3 catalogados, ~1 atualizado, -2 quarentena")


def test_tick_subject_includes_conflicts_and_renamed_signs_when_present():
    report = TickReport(tick="01JTICK0000000000000000000",
                        conflicts=1, renamed=4)
    assert _tick_subject(report) == "snapshot: ⚠1 dup, →4 rename"


def test_tick_subject_all_relevant_zero_falls_back_to_generic_message():
    # stale>0 sozinho não conta pro subject (sem sinal definido pra ele).
    report = TickReport(tick="01JTICK0000000000000000000", stale=5)
    assert _tick_subject(report) == "snapshot: metadados atualizados"


def test_tick_subject_zero_report_falls_back_to_generic_message():
    report = TickReport(tick="01JTICK0000000000000000000")
    assert _tick_subject(report) == "snapshot: metadados atualizados"


def test_tick_body_shows_all_seven_categories_with_extras_and_footer():
    report = TickReport(tick="01JTICK0000000000000000000", processed=3,
                        literate=2, updated=1, absorbed=4, quarantined=2,
                        conflicts=1, renamed=0, stale=0)
    body = _tick_body(report)
    assert body == (
        "cataloga:    3  (2 alfabetizados)\n"
        "atualiza:    1  (source-keyed in-place)\n"
        "absorve:     4\n"
        "quarentena:  2  (duplicata exata)\n"
        "near-dup:    1  (marcado conflito)\n"
        "rename:      0\n"
        "stale:       0\n"
        "\n"
        "tick: 01JTICK0000000000000000000\n"
        f"schema: config={SCHEMA_VERSION} index={INDEX_SCHEMA_VERSION}"
        f" contract={CONTRACT_VERSION}")


def test_tick_body_omits_parenthetical_extras_when_counts_zero():
    report = TickReport(tick="01JTICK0000000000000000000")
    body = _tick_body(report)
    for line in body.splitlines()[:7]:
        assert "(" not in line
    assert body.splitlines()[0] == "cataloga:    0"


def test_tick_body_has_seven_fixed_lines():
    body = _tick_body(TickReport(tick="01JTICK0000000000000000000"))
    linhas, _ = body.split("\n\n")
    assert len(linhas.splitlines()) == 7


def test_absorbed_appears_in_subject(tmp_path):
    report = TickReport(tick="01JTICK0000000000000000000", absorbed=2)
    assert _tick_subject(report) == "snapshot: ↻2 absorvido"


def test_contract_version_is_four():
    """Aditivo, mas o precedente da v0.6 (campo `snapshot`) bumpou por
    aditivo: consumidor que fixa versão merece saber que o envelope
    cresceu."""
    assert CONTRACT_VERSION == 4


def test_tick_body_processed_extra_gated_on_literate_not_processed():
    # processed>0 sem nenhum alfabetizado (ex.: só órfãos/skill-items
    # adotados) não deve exibir a glosa "(N alfabetizados)".
    report = TickReport(tick="01JTICK0000000000000000000", processed=3,
                        literate=0)
    body = _tick_body(report)
    assert body.splitlines()[0] == "cataloga:    3"


def test_commit_tick_returns_none_when_tree_clean(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    report = TickReport(tick="01JTICK0000000000000000000", processed=3)
    assert commit_tick(home, report) is None


def test_commit_tick_without_git_returns_none(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.setattr(snapshot, "_AVAIL", None)
    report = TickReport(tick="01JTICK0000000000000000000", processed=1)
    assert commit_tick(home, report) is None


def test_commit_tick_creates_commit_with_subject_and_body(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    report = TickReport(tick="01JTICK0000000000000000000", processed=1,
                        literate=1)

    sha = commit_tick(home, report)

    assert sha is not None
    log = subprocess.run(
        ["git", "-C", str(home.library), "log", "-1", "--pretty=%s%n%b"],
        capture_output=True, text=True, check=True).stdout
    assert log.startswith("snapshot: +1 catalogados\n")
    assert "cataloga:    1  (1 alfabetizados)" in log
    assert f"tick: {report.tick}" in log


def test_commit_tick_zero_report_but_dirty_tree_uses_metadata_fallback(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    report = TickReport(tick="01JTICK0000000000000000000")  # todo-zero

    sha = commit_tick(home, report)

    assert sha is not None
    subject = subprocess.run(
        ["git", "-C", str(home.library), "log", "-1", "--pretty=%s"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert subject == "snapshot: metadados atualizados"


def test_commit_tick_idempotent_second_call_without_changes_is_none(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    (home.library / "nota.md").write_text("conteudo\n")
    report = TickReport(tick="01JTICK0000000000000000000", processed=1)
    first = commit_tick(home, report)
    assert first is not None
    assert commit_tick(home, report) is None


# ── restore (spec §5 / Task 6) ────────────────────────────────────────

def _note(home, name, nid, title, body):
    (home.library / f"{name}.md").write_text(
        f"---\nid: {nid}\ntitle: {title}\n---\n{body}\n")


def _head(home):
    return subprocess.run(
        ["git", "-C", str(home.library), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()


def _status(home):
    return subprocess.run(
        ["git", "-C", str(home.library), "status", "--porcelain"],
        capture_output=True, text=True, check=True).stdout


def _log_subjects(home):
    return subprocess.run(
        ["git", "-C", str(home.library), "log", "--format=%s"],
        capture_output=True, text=True, check=True).stdout.splitlines()


def _tracked(home):
    return subprocess.run(
        ["git", "-C", str(home.library), "ls-files"],
        capture_output=True, text=True, check=True).stdout.split()


def test_restore_unknown_ref_raises_and_leaves_tree_and_head_intact(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "conteudo a")
    commit(home, "feat: a")
    head_before = _head(home)
    status_before = _status(home)

    with pytest.raises(SnapshotError):
        restore(home, "no-such-ref")

    assert _head(home) == head_before
    assert _status(home) == status_before
    assert not (home.root / "index.lock").exists()


def test_restore_autosaves_dirty_tree_before_materializing(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "conteudo original")
    ref1 = commit(home, "feat: a")
    (home.library / "a.md").write_text(
        "---\nid: 01A\ntitle: A\n---\nconteudo sujo alterado\n")

    result = restore(home, ref1)

    assert result["ok"] is True
    assert result["autosaved"] is not None
    assert "snapshot: autosave antes de restore" in _log_subjects(home)
    dirty_blob = subprocess.run(
        ["git", "-C", str(home.library), "show",
         f"{result['autosaved']}:a.md"],
        capture_output=True, text=True, check=True).stdout
    assert "conteudo sujo alterado" in dirty_blob
    assert (home.library / "a.md").read_text() == (
        "---\nid: 01A\ntitle: A\n---\nconteudo original\n")


def test_restore_materializes_ref_tree_exactly_add_and_delete(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "manter", "01K", "Manter", "fica")
    _note(home, "del-pos-ref", "01D", "Del", "volta")
    ref1 = commit(home, "feat: estado 1")

    (home.library / "del-pos-ref.md").unlink()
    _note(home, "add-pos-ref", "01N", "Novo", "novo")
    commit(home, "feat: estado 2")

    result = restore(home, ref1)

    assert result["ok"] is True
    assert not (home.library / "add-pos-ref.md").exists()
    assert (home.library / "del-pos-ref.md").read_text() == (
        "---\nid: 01D\ntitle: Del\n---\nvolta\n")
    assert (home.library / "manter.md").exists()
    tracked = _tracked(home)
    assert "add-pos-ref.md" not in tracked
    assert "del-pos-ref.md" in tracked


def test_restore_reindexes_and_query_finds_restored_state(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "nota", "01Q", "Nota Original", "texto original distintivo")
    ref1 = commit(home, "feat: estado 1")
    reindex(home)

    _note(home, "nota", "01Q", "Nota Original", "texto mutante substituto")
    commit(home, "feat: estado 2")
    reindex(home)

    result = restore(home, ref1)

    assert result["ok"] is True
    assert result["reindex"]["indexed"] == 1
    res = query(home, "distintivo")["results"]
    assert any(r["slug"] == "nota" for r in res)
    assert query(home, "substituto")["results"] == []


def test_restore_noop_when_ref_equals_head(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")
    head_before = _head(home)

    result = restore(home, head_before)

    assert result["ok"] is True
    assert result["restored_to"] == head_before
    assert result["autosaved"] is None
    assert result["new_head"] == head_before
    assert "reindex" in result
    assert _head(home) == head_before


def test_restore_forward_only_head_advances_and_ref_stays_reachable(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "v1")
    ref1 = commit(home, "feat: v1")
    _note(home, "a", "01A", "A", "v2")
    commit(home, "feat: v2")

    result = restore(home, ref1)

    new_head = result["new_head"]
    assert new_head != ref1
    ancestor = subprocess.run(
        ["git", "-C", str(home.library), "merge-base", "--is-ancestor",
         ref1, new_head], check=False)
    assert ancestor.returncode == 0


def test_restore_reindex_failure_returns_ok_false_without_raising(
        tmp_path, monkeypatch):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "v1")
    ref1 = commit(home, "feat: v1")
    _note(home, "a", "01A", "A", "v2")
    commit(home, "feat: v2")

    def _boom(home_arg, con_arg):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot, "_reindex_locked", _boom)

    result = restore(home, ref1)

    assert result["ok"] is False
    assert result["need"] == "reindex"
    assert "boom" in result["reindex_error"]
    # tree já materializado em disco apesar da falha do reindex
    assert (home.library / "a.md").read_text() == (
        "---\nid: 01A\ntitle: A\n---\nv1\n")
    assert not (home.root / "index.lock").exists()


def test_restore_reuses_reindex_locked_without_reopening_lock(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "v1")
    ref1 = commit(home, "feat: v1")
    _note(home, "a", "01A", "A", "v2")
    commit(home, "feat: v2")

    result = restore(home, ref1)

    assert result["ok"] is True
    assert not (home.root / "index.lock").exists()
    # lock foi liberado corretamente (sem lock aninhado preso) — um
    # reindex() subsequente, que abre o IndexLock por conta própria,
    # roda sem travar.
    reindex(home)


# ── list_snapshots / commit_manual / restore_dry_run (§7.1-7.3, Task 7) ──

def test_list_snapshots_empty_when_no_repo(tmp_path):
    home = _home(tmp_path)
    assert list_snapshots(home) == []


def test_list_snapshots_empty_without_git(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.setattr(snapshot, "_AVAIL", None)
    assert list_snapshots(home) == []


def test_list_snapshots_most_recent_first_respects_limit(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "1")
    commit(home, "feat: um")
    _note(home, "b", "01B", "B", "2")
    commit(home, "feat: dois")
    _note(home, "c", "01C", "C", "3")
    commit(home, "feat: tres")

    out = list_snapshots(home, limit=2)

    assert len(out) == 2
    assert out[0]["subject"] == "feat: tres"
    assert out[1]["subject"] == "feat: dois"
    assert set(out[0]) == {"sha", "ts", "subject"}


def test_commit_manual_noop_without_changes(tmp_path):
    home = _home(tmp_path)
    assert commit_manual(home) == {"ok": True, "snapshot": None,
                                   "changed": False}


def test_commit_manual_commits_dirty_tree_with_stat_body(tmp_path):
    home = _home(tmp_path)
    _note(home, "a", "01A", "A", "conteudo")

    result = commit_manual(home)

    assert result["ok"] is True
    assert result["changed"] is True
    assert result["snapshot"] is not None
    log = subprocess.run(
        ["git", "-C", str(home.library), "log", "-1", "--pretty=%s%n%b"],
        capture_output=True, text=True, check=True).stdout
    assert log.startswith("snapshot: manual (1 arquivos)\n")
    assert "a.md" in log


def test_commit_manual_second_call_without_new_changes_is_noop(tmp_path):
    home = _home(tmp_path)
    _note(home, "a", "01A", "A", "conteudo")
    assert commit_manual(home)["changed"] is True
    assert commit_manual(home) == {"ok": True, "snapshot": None,
                                   "changed": False}


def test_restore_dry_run_invalid_ref_is_error_and_never_mutates(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")
    head_before = _head(home)

    result = restore_dry_run(home, "no-such-ref")

    assert result == {"ok": False, "restored_to": "no-such-ref",
                      "error": "ref inválido: no-such-ref"}
    assert _head(home) == head_before


def test_restore_dry_run_valid_ref_shows_diff_stat_and_dirty_flag(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "v1")
    ref1 = commit(home, "feat: v1")
    _note(home, "a", "01A", "A", "v2")
    commit(home, "feat: v2")
    (home.library / "b.md").write_text("dirty\n")
    head_before = _head(home)

    result = restore_dry_run(home, ref1)

    assert result["ok"] is False
    assert result["dry_run"] is True
    assert result["restored_to"] == ref1
    assert "a.md" in result["would_change"]
    assert result["dirty"] is True
    # nada foi tocado: HEAD e o dirty extra continuam intactos.
    assert _head(home) == head_before
    assert (home.library / "b.md").exists()


def test_restore_dry_run_clean_tree_has_dirty_flag_false(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "v1")
    ref1 = commit(home, "feat: v1")

    assert restore_dry_run(home, ref1)["dirty"] is False


# ── push (§7.4/§7.5/§11 — file:// local, sem rede real) ─────────────

def _bare_remote(tmp_path_factory):
    remote_dir = tmp_path_factory.mktemp("remote") / "lib.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote_dir)],
                  check=True)
    return f"file://{remote_dir}"


def test_push_without_git_returns_clear_error(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setenv("PATH", "/nonexistent-bin")
    monkeypatch.setattr(snapshot, "_AVAIL", None)

    result = push(home)

    assert result == {"ok": False, "pushed": False, "remote": None,
                      "error": "git indisponível — instale git"}


def test_push_without_repo_returns_error(tmp_path):
    home = _home(tmp_path)
    result = push(home)
    assert result["ok"] is False
    assert "não versionada" in result["error"]


def test_push_without_remote_configured_asks_for_set_remote(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")

    result = push(home)

    assert result["ok"] is False
    assert result["remote"] is None
    assert "--set-remote" in result["error"]


def test_push_succeeds_to_local_file_remote(tmp_path, tmp_path_factory):
    remote_url = _bare_remote(tmp_path_factory)
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")
    set_remote(home, remote_url)

    assert push(home) == {"ok": True, "pushed": True, "remote": remote_url}


def test_push_remote_url_param_syncs_git_remote_before_pushing(
        tmp_path, tmp_path_factory):
    remote_url = _bare_remote(tmp_path_factory)
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")

    result = push(home, remote_url=remote_url)

    assert result["ok"] is True
    configured = subprocess.run(
        ["git", "-C", str(home.library), "remote", "get-url", "neurata"],
        capture_output=True, text=True, check=True).stdout.strip()
    assert configured == remote_url


def test_push_failure_reports_error_without_raising(tmp_path):
    home = _home(tmp_path)
    ensure_repo(home)
    _note(home, "a", "01A", "A", "x")
    commit(home, "feat: a")
    set_remote(home, "file:///nao/existe/nesse/caminho.git")

    result = push(home)

    assert result["ok"] is False
    assert result["pushed"] is False
    assert result["error"]
