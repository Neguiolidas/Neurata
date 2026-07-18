"""tests/test_cli.py"""
import json

from neurata.cli import main


def test_deposit_json_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path))
    rc = main(["deposit", "conhecimento novo", "--title", "T", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 1
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
    assert out["contract_version"] == 1
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
