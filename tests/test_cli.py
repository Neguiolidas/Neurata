"""tests/test_cli.py"""
import json

from armarium.cli import main


def test_deposit_json_roundtrip(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARMARIUM_HOME", str(tmp_path))
    rc = main(["deposit", "conhecimento novo", "--title", "T", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["contract_version"] == 1
    assert out["ok"] is True
    assert out["command"] == "deposit"
    assert out["result"]["action"] == "created"


def test_deposit_stdin(tmp_path, monkeypatch, capsys):
    import io
    monkeypatch.setenv("ARMARIUM_HOME", str(tmp_path))
    monkeypatch.setattr("sys.stdin", io.StringIO("via stdin"))
    rc = main(["deposit", "-", "--json"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["result"]["action"] == "created"


def test_reindex_and_doctor_flow(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARMARIUM_HOME", str(tmp_path))
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
    monkeypatch.setenv("ARMARIUM_HOME", str(tmp_path))
    rc = main(["deposit", "--file", str(tmp_path / "nada.md"), "--json"])
    assert rc == 2
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False
    assert out["error"]["code"] == "DepositError"


def test_human_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ARMARIUM_HOME", str(tmp_path))
    rc = main(["deposit", "algo humano", "--title", "Nota"])
    assert rc == 0
    assert "created" in capsys.readouterr().out
