"""tests/test_cli.py"""
import json
import subprocess

from neurata.cli import main


def test_deposit_json_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["deposit", "conhecimento novo", "--title", "T", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 3
    assert out["ok"] is True
    assert out["command"] == "deposit"
    assert out["result"]["action"] == "created"


def test_deposit_stdin(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("via stdin"))
    rc = main(["deposit", "-", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["result"]["action"] == "created"


def test_reindex_and_doctor_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    capsys.readouterr()
    rc = main(["reindex", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["result"]["indexed"] == 1
    main(["snapshot"])  # commit limpo -> snapshot check "ok" (senão warn/rc1)
    main(["tick"])  # last-tick check exige tick recente -> rc0
    capsys.readouterr()
    rc = main(["doctor", "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["result"]["checks"]
    assert all(c["status"] == "ok" for c in out["result"]["checks"])


def test_error_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["deposit", "--file", str(tmp_path / "nada.md"), "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "DepositError"


def test_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["deposit", "algo humano", "--title", "Nota"])
    assert rc == 0
    assert "created" in capsys.readouterr().out


def test_json_flag_global_position(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["--json", "deposit", "x"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 3
    assert out["ok"] is True
    assert out["command"] == "deposit"
    assert out["result"]["action"] == "created"


def test_unwritable_home_stays_inside_error_envelope(
        tmp_path, monkeypatch, capsys):
    # NEURATA_HOME apontando p/ um arquivo (não diretório) faz
    # home.init() levantar NotADirectoryError/OSError ao tentar mkdir.
    # Isso precisa ficar dentro do boundary de erro, não vazar traceback.
    impossible = tmp_path / "file"
    impossible.write_text("sou um arquivo, não um diretório")
    monkeypatch.setenv("NEURATA_HOME", str(impossible))

    rc = main(["--json", "deposit", "x"])

    assert rc == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False
    assert "Traceback" not in captured.out + captured.err


def test_doctor_json_envelope_ok_reflects_rc(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["reindex"])
    main(["snapshot"])  # commit limpo -> snapshot check "ok" (senão warn/rc1)
    main(["tick"])  # last-tick check exige tick recente -> rc0
    capsys.readouterr()

    # Doctor tudo ok -> envelope ok:true.
    rc_ok = main(["doctor", "--json"])
    out_ok = json.loads(capsys.readouterr().out)
    assert rc_ok == 0
    assert out_ok["ok"] is True

    # Corrompe index.db para forçar check "fail" -> rc==2, envelope
    # precisa refletir ok:false (antes do fix ficava hardcoded true).
    home_index = tmp_path / "index.db"
    home_index.write_bytes(b"isto nao e um sqlite valido")

    rc_fail = main(["doctor", "--json"])
    out_fail = json.loads(capsys.readouterr().out)
    assert rc_fail == 2
    assert out_fail["ok"] is False
    assert out_fail["result"]["checks"]


def test_unexpected_exception_never_leaks_traceback(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr("neurata.cli.deposit", boom)
    rc = main(["deposit", "x", "--json"])
    assert rc == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False
    assert out["error"]["code"] == "RuntimeError"
    assert out["error"]["message"] == "boom"
    assert "Traceback" not in captured.out + captured.err


def test_query_json_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteúdo sobre vetores e RRF", "--title", "Vetores"])
    main(["reindex"])
    capsys.readouterr()
    rc = main(["query", "vetores", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "query"
    assert out["result"]["results"][0]["via"] == "lexical"


def test_query_before_reindex_remediation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["query", "x", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "reindex" in out["error"]["message"]


def test_bad_args_emit_json_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["query", "x", "--limit", "nan", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "UsageError"


def test_top_level_invalid_command_emits_json_envelope(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["bogus-command", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["command"] is None
    assert out["error"]["code"] == "UsageError"
    assert "invalid choice" in out["error"]["message"]


def test_unrecognized_flag_emits_json_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["--nao-existe", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "UsageError"
    assert "unrecognized arguments" in out["error"]["message"]


def test_bad_args_without_json_flag_dont_leak_argparse_usage_stdout(
        tmp_path, monkeypatch, capsys):
    """Sem --json, o erro vai formatado pro stderr — não é o usage cru
    do argparse (que hoje sairia direto, fora do envelope, via stderr
    também, mas sem passar pelo mesmo formatador de erro do resto da
    CLI)."""
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["bogus-command"])
    assert rc == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "UsageError" in captured.err
    assert "invalid choice" in captured.err


_LONG_BODY = (
    "## Seção 1\n\n"
    "Primeiro parágrafo da seção um com bastante texto pra simular "
    "conteúdo real que algum dia precisa ser compactado pelo sistema.\n\n"
    "Segundo parágrafo da seção um — este some do summary, que só pega "
    "o primeiro parágrafo de cada seção.\n\n"
    "## Seção 2\n\n"
    "Parágrafo único da seção dois."
)


def test_compact_json_produces_summary_and_archives_full(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", _LONG_BODY, "--title", "Compactável", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    rc = main(["compact", eid, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "compact"
    result = out["result"]
    assert result["action"] == "compacted"
    assert result["id"] == eid
    assert result["archived"]

    path = tmp_path / result["path"]
    from neurata.frontmatter import parse
    meta, body = parse(path.read_text(encoding="utf-8"))
    assert meta["derived_from"] == result["archived"]
    # summary segundo parágrafo da Seção 1 some, mas o resto persiste.
    assert "Segundo parágrafo" not in body
    assert "Seção 2" in body


def test_compact_noop_when_body_already_is_summary(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "corpo curtinho sem headings", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    rc = main(["compact", eid, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["action"] == "noop"
    assert out["result"]["id"] == eid


def test_compact_then_expand_restore_is_byte_identical(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", _LONG_BODY, "--title", "Roundtrip", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    from neurata.frontmatter import parse
    rc = main(["compact", eid, "--json"])
    assert rc == 0
    compacted = json.loads(capsys.readouterr().out)["result"]
    real_path = tmp_path / compacted["path"]
    _, summary_body = parse(real_path.read_text(encoding="utf-8"))
    assert summary_body != _LONG_BODY

    rc = main(["expand", eid, "--grain", "full", "--json"])
    assert rc == 0
    full_out = json.loads(capsys.readouterr().out)["result"]
    assert full_out["text"] == _LONG_BODY

    rc = main(["expand", eid, "--restore", "--json"])
    assert rc == 0
    restored = json.loads(capsys.readouterr().out)["result"]
    assert restored["action"] == "restored"
    _, restored_body = parse(real_path.read_text(encoding="utf-8"))
    assert restored_body == _LONG_BODY


def test_expand_grain_card_and_summary_via_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", _LONG_BODY, "--title", "Grãos", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    rc = main(["expand", eid, "--grain", "card", "--json"])
    assert rc == 0
    card = json.loads(capsys.readouterr().out)["result"]["text"]
    assert card and "\n" not in card

    rc = main(["expand", eid, "--grain", "summary", "--json"])
    assert rc == 0
    summary = json.loads(capsys.readouterr().out)["result"]["text"]
    assert "Segundo parágrafo" not in summary
    assert "Seção 2" in summary


def test_expand_restore_noop_when_not_compacted(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "nunca foi compactado", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    rc = main(["expand", eid, "--restore", "--json"])
    assert rc == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["action"] == "noop"
    assert result["id"] == eid


def test_expand_human_output_prints_text(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "um corpo qualquer", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()

    rc = main(["expand", eid])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "um corpo qualquer"


def test_expand_bad_grain_emits_usage_error(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "x", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    capsys.readouterr()

    rc = main(["expand", eid, "--grain", "invalido", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "UsageError"


def test_shelf_inventory_json_marks_never_consulted(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "nunca foi consultado", "--title", "T", "--json"])
    capsys.readouterr()
    main(["reindex"])
    capsys.readouterr()

    rc = main(["shelf", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "shelf"
    items = out["result"]["items"]
    assert items
    assert items[0]["candidato_arquivamento"] is True
    assert items[0]["impressions"] == 0 and items[0]["expands"] == 0


def test_shelf_insights_json_after_expand(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteúdo consultado depois", "--title", "T", "--json"])
    eid = json.loads(capsys.readouterr().out)["result"]["id"]
    main(["reindex"])
    capsys.readouterr()
    main(["expand", eid, "--json"])
    capsys.readouterr()

    rc = main(["shelf", "--insights", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    result = out["result"]
    assert any(c["id"] == eid for c in result["top_expands"])
    assert "total_conflitos" in result


def test_shelf_conflicts_json_reports_manual_conflicts_with(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "a", "--title", "A", "--json"])
    capsys.readouterr()
    main(["reindex"])
    capsys.readouterr()

    lib = tmp_path / "library"
    (lib / "conflitante.md").write_text(
        "---\nid: 01CONFLICT\ntitle: Conflitante\n"
        "conflicts_with: [\"a\"]\n---\ncorpo\n")

    rc = main(["shelf", "--conflicts", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["conflicts_with"]
    assert out["result"]["conflicts_with"][0]["conflicts_with"] == ["a"]


def test_shelf_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo pro shelf", "--title", "T"])
    main(["reindex"])
    capsys.readouterr()

    rc = main(["shelf"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "candidato-arquivamento" in out


def test_tick_json_empty_inbox_is_noop(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["tick", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["command"] == "tick"
    assert out["result"]["processed"] == 0
    assert out["result"]["errors"] == []


def test_tick_json_catalogs_inbox_item(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteúdo bruto"])
    capsys.readouterr()

    rc = main(["tick", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 3
    result = out["result"]
    assert result["processed"] == 1
    assert "snapshot" in result
    assert result["snapshot"] is not None
    assert not list((tmp_path / "inbox").glob("*.md"))
    assert list((tmp_path / "library").glob("*.md"))


def test_tick_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteúdo humano"])
    capsys.readouterr()

    rc = main(["tick"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "processed=1" in out


def test_tick_budget_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "um"])
    main(["deposit", "dois"])
    capsys.readouterr()

    rc = main(["tick", "--budget", "1", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["processed"] == 1
    assert len(list((tmp_path / "inbox").glob("*.md"))) == 1


def test_tick_exit_code_2_on_item_errors(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    from neurata.home import NeurataHome
    home = NeurataHome(tmp_path)
    home.init()
    bad_target = tmp_path / "fora_da_inbox.md"
    bad_target.write_text("alvo fora do inbox\n")
    (home.inbox / "link.md").symlink_to(bad_target)
    capsys.readouterr()

    rc = main(["tick", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["result"]["errors"]


def test_tick_exit_code_1_on_structural_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    from neurata.home import NeurataHome
    from neurata.indexdb import connect
    home = NeurataHome(tmp_path)
    home.init()
    con = connect(home)
    con.execute("INSERT OR REPLACE INTO meta VALUES "
               "('index_schema_version', ?)", ("0",))
    con.commit()
    con.close()
    capsys.readouterr()

    rc = main(["tick", "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "TickStructuralError"


def _make_skill(base, name, description="faz coisas quando X"):
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n"
        f"Corpo de {name}.\n", encoding="utf-8")


def test_harvest_default_target_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "foo")
    monkeypatch.setenv("NEURATA_CLAUDE_SKILLS_DIR", str(skills_dir))
    capsys.readouterr()

    rc = main(["harvest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "harvested=1 updated=0 removed=0 skipped=0" in out


def test_harvest_json_envelope(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    skills_dir = tmp_path / "skills"
    _make_skill(skills_dir, "foo")
    monkeypatch.setenv("NEURATA_CLAUDE_SKILLS_DIR", str(skills_dir))
    capsys.readouterr()

    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["command"] == "harvest"
    result = out["result"]
    assert result["target"] == "claude-code"
    assert result["harvested"] == 1
    assert result["updated"] == 0
    assert result["removed"] == 0
    assert result["skipped"] == []


def test_harvest_bad_target(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    capsys.readouterr()

    rc = main(["harvest", "bogus", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "UsageError"
    assert "invalid choice: 'bogus'" in out["error"]["message"]
    assert "claude-code" in out["error"]["message"]


def test_harvest_schema_mismatch(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    from neurata.home import NeurataHome
    from neurata.indexdb import connect
    home = NeurataHome(tmp_path)
    home.init()
    con = connect(home)
    con.execute("INSERT OR REPLACE INTO meta VALUES "
               "('index_schema_version', ?)", ("5",))
    con.commit()
    con.close()
    capsys.readouterr()

    rc = main(["harvest", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "reindex" in out["error"]["message"]


# ── snapshot: commit manual, --list, --restore, --push, --set-remote ──
# (Task 7 / spec §7)

def _first_lib_file(tmp_path):
    return next((tmp_path / "library").glob("*.md"))


def test_snapshot_manual_commit_json_noop_then_commits_dirty_change(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo", "--title", "T"])
    main(["tick"])
    capsys.readouterr()

    # tick já commitou tudo — sem novas mudanças, snapshot manual é noop.
    rc = main(["snapshot", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 3
    assert out["ok"] is True
    assert out["command"] == "snapshot"
    assert out["result"] == {"ok": True, "snapshot": None, "changed": False}

    # edição direta na library (fora do fluxo tick) -> commita.
    lib_file = _first_lib_file(tmp_path)
    lib_file.write_text(lib_file.read_text() + "\nlinha extra\n")

    rc = main(["snapshot", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["changed"] is True
    assert out["result"]["snapshot"] is not None


def test_snapshot_human_output_manual_commit(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot"])
    assert rc == 0
    assert "changed=False" in capsys.readouterr().out


def test_snapshot_list_json_returns_recent_snapshots_and_respects_limit(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "um"])
    main(["tick"])
    capsys.readouterr()
    main(["deposit", "dois"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot", "--list", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    snaps = out["result"]["snapshots"]
    assert len(snaps) >= 2
    assert set(snaps[0]) == {"sha", "ts", "subject"}

    rc = main(["snapshot", "--list", "-n", "1", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["result"]["snapshots"]) == 1


def test_snapshot_list_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot", "--list"])
    assert rc == 0
    assert "snapshot:" in capsys.readouterr().out


def test_snapshot_restore_without_yes_is_dry_run_and_never_mutates(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteudo v1", "--title", "T"])
    main(["tick"])
    capsys.readouterr()
    main(["snapshot", "--list", "--json"])
    ref1 = json.loads(capsys.readouterr().out)["result"]["snapshots"][0]["sha"]

    lib_file = _first_lib_file(tmp_path)
    before = lib_file.read_text()
    lib_file.write_text(before + "\nmudanca sem commit\n")

    rc = main(["snapshot", "--restore", ref1, "--json"])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["result"]["dry_run"] is True
    assert out["result"]["dirty"] is True
    # nada foi tocado.
    assert lib_file.read_text() == before + "\nmudanca sem commit\n"


def test_snapshot_restore_dry_run_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()
    main(["snapshot", "--list", "--json"])
    ref1 = json.loads(capsys.readouterr().out)["result"]["snapshots"][0]["sha"]

    rc = main(["snapshot", "--restore", ref1])
    assert rc == 1
    assert "dry-run" in capsys.readouterr().out


def test_snapshot_restore_with_yes_materializes_ref(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "conteudo v1", "--title", "T"])
    main(["tick"])
    capsys.readouterr()
    main(["snapshot", "--list", "--json"])
    ref1 = json.loads(capsys.readouterr().out)["result"]["snapshots"][0]["sha"]

    lib_file = _first_lib_file(tmp_path)
    lib_file.write_text(lib_file.read_text().replace("v1", "v2 mutante"))
    main(["snapshot", "--json"])  # commita v2
    capsys.readouterr()

    rc = main(["snapshot", "--restore", ref1, "--yes", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["restored_to"] == ref1
    assert "reindex" in out["result"]
    assert "v2 mutante" not in lib_file.read_text()


def test_snapshot_restore_invalid_ref_without_yes_is_error_exit_2(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    # Ref inválido é erro (tem "error" no result) mesmo sem --yes — dry-run
    # só devolve rc=1 quando o ref é válido; rc=2 aqui, igual ao caminho
    # --yes (ambos nunca mutam nada).
    rc = main(["snapshot", "--restore", "no-such-ref", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "no-such-ref" in out["result"]["error"]


def test_snapshot_restore_invalid_ref_with_yes_raises_into_error_envelope(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot", "--restore", "no-such-ref", "--yes", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "SnapshotError"


def test_snapshot_push_without_remote_returns_error_exit_2(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot", "--push", "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert "--set-remote" in out["result"]["error"]


def test_snapshot_set_remote_persists_config_and_push_succeeds(
        tmp_path, tmp_path_factory, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    remote_dir = tmp_path_factory.mktemp("remote") / "lib.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote_dir)],
                  check=True)
    remote_url = f"file://{remote_dir}"

    main(["deposit", "algo"])
    main(["tick"])
    capsys.readouterr()

    rc = main(["snapshot", "--set-remote", remote_url, "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["remote"] == remote_url

    cfg = json.loads((tmp_path / "config.json").read_text())
    assert cfg["snapshot"]["remote"] == remote_url
    assert "schema_version" in cfg  # preserva chave existente

    rc = main(["snapshot", "--push", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"] == {"ok": True, "pushed": True, "remote": remote_url}


def test_snapshot_auto_push_on_tick_pushes_to_configured_remote(
        tmp_path, tmp_path_factory, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    remote_dir = tmp_path_factory.mktemp("remote") / "lib.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote_dir)],
                  check=True)
    remote_url = f"file://{remote_dir}"

    main(["snapshot", "--set-remote", remote_url])
    capsys.readouterr()
    cfg_path = tmp_path / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["snapshot"]["auto_push"] = True
    cfg_path.write_text(json.dumps(cfg))

    main(["deposit", "algo pra versionar"])
    capsys.readouterr()

    rc = main(["tick", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["snapshot"] is not None

    # o bare repo recém-criado tem HEAD apontando pro branch default do
    # host (ex.: master), que nunca existe — checa direto o ref "main"
    # (o branch que ensure_repo() usa), não HEAD.
    remote_log = subprocess.run(
        ["git", "-C", str(remote_dir), "log", "--oneline", "main"],
        capture_output=True, text=True, check=True).stdout
    assert remote_log.strip() != ""


def test_snapshot_auto_push_failure_does_not_fail_tick_but_logs(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    main(["snapshot", "--set-remote", "file:///nao/existe/nesse/caminho.git"])
    capsys.readouterr()
    cfg_path = tmp_path / "config.json"
    cfg = json.loads(cfg_path.read_text())
    cfg["snapshot"]["auto_push"] = True
    cfg_path.write_text(json.dumps(cfg))

    main(["deposit", "algo"])
    capsys.readouterr()

    rc = main(["tick", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["snapshot"] is not None

    log = (tmp_path / "logs" / "snapshot.jsonl").read_text()
    assert "push_error" in log
