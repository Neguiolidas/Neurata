"""tests/test_deposit.py"""
import pytest

from neurata.deposit import DepositError, deposit
from neurata.frontmatter import parse
from neurata.home import NeurataHome


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_deposit_text_creates_inbox_file(tmp_path):
    home = _home(tmp_path)
    rec = deposit(home, "Aprendi que FTS5 precisa de probe.\nDetalhe.",
                  title="FTS5 probe")
    assert rec["action"] == "created"
    files = list(home.inbox.glob("*.md"))
    assert len(files) == 1
    assert files[0].name.endswith("-fts5-probe.md")
    meta, body = parse(files[0].read_text())
    assert meta["id"] == rec["id"]
    assert meta["type"] == "note"
    assert meta["title"] == "FTS5 probe"
    assert meta["description"] == "Aprendi que FTS5 precisa de probe."
    assert meta["source"]["host"]
    assert "Detalhe." in body


def test_duplicate_appends_event_not_file(tmp_path):
    home = _home(tmp_path)
    r1 = deposit(home, "mesmo conteúdo")
    r2 = deposit(home, "mesmo conteúdo", agent="claude")
    assert r2["action"] == "duplicate"
    assert r2["id"] == r1["id"]
    assert len(list(home.inbox.glob("*.md"))) == 1
    log = home.read_log("deposits")
    assert len(log) == 2
    assert log[1]["action"] == "duplicate"
    assert log[1]["envelope"]["agent"] == "claude"


def test_deposit_from_file(tmp_path):
    home = _home(tmp_path)
    src = tmp_path / "doc.md"
    src.write_text("# Título do doc\ncorpo")
    rec = deposit(home, file=src)
    assert rec["action"] == "created"
    meta, _ = parse(list(home.inbox.glob("*.md"))[0].read_text())
    assert meta["source"]["origin"] == str(src)


def test_size_cap(tmp_path):
    with pytest.raises(DepositError, match="2000000"):
        deposit(_home(tmp_path), "x" * 2_000_001)


def test_empty_and_whitespace_only_rejected(tmp_path):
    home = _home(tmp_path)
    with pytest.raises(DepositError, match="vazio"):
        deposit(home, "")
    with pytest.raises(DepositError, match="vazio"):
        deposit(home, "   \n\t  ")
    # arquivo whitespace-only também é rejeitado
    src = tmp_path / "blank.md"
    src.write_text("  \n  ")
    with pytest.raises(DepositError, match="vazio"):
        deposit(home, file=src)
    assert list(home.inbox.glob("*.md")) == []
    assert home.read_log("deposits") == []


def test_requires_exactly_one_source(tmp_path):
    home = _home(tmp_path)
    with pytest.raises(DepositError):
        deposit(home)
    with pytest.raises(DepositError):
        deposit(home, "a", file=home.root / "nada.md")


def test_missing_file(tmp_path):
    home = _home(tmp_path)
    with pytest.raises(DepositError, match="não encontrado"):
        deposit(home, file=home.root / "nao-existe.md")


def test_redeposit_after_inbox_file_deleted_creates_fresh_entry(tmp_path):
    home = _home(tmp_path)
    r1 = deposit(home, "conteúdo que será apagado")
    assert r1["action"] == "created"
    dup = deposit(home, "conteúdo que será apagado")
    assert dup["action"] == "duplicate"
    (home.root / r1["path"]).unlink()

    r2 = deposit(home, "conteúdo que será apagado")

    assert r2["action"] == "created"
    assert r2["id"] != r1["id"]
    assert (home.root / r2["path"]).is_file()
    log = home.read_log("deposits")
    assert len(log) == 3
    created = [e for e in log if e["action"] == "created"]
    assert len(created) == 2


def test_multiline_title_is_sanitized_and_frontmatter_reparses(tmp_path):
    home = _home(tmp_path)
    rec = deposit(home, "corpo qualquer", title="line1\nline2")
    assert rec["action"] == "created"
    text = (home.root / rec["path"]).read_text()
    meta, _ = parse(text)
    assert meta["title"] == "line1 line2"


def test_git_envelope_flattened_in_frontmatter_nested_in_log(tmp_path):
    # Rodar do root do repo Neurata dá git context de graça no envelope.
    home = _home(tmp_path)
    rec = deposit(home, "conteúdo com git context")
    meta, _ = parse(list(home.inbox.glob("*.md"))[0].read_text())
    # source é dict 1 nível de escalares — round-trip via parse prova subset.
    assert meta["source"]["git_commit"]
    assert meta["source"]["git_branch"]
    assert "git" not in meta["source"]
    # O log guarda o envelope COMPLETO, com git aninhado intacto.
    event = home.read_log("deposits")[0]
    assert event["id"] == rec["id"]
    assert event["envelope"]["git"]["commit"] == meta["source"]["git_commit"]
