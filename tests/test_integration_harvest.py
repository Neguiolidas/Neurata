"""tests/test_integration_harvest.py — fluxo real end-to-end (Fase 7).

Cobre docs/superpowers/plans/2026-07-18-neurata-v0.5-harvest-plan.md §Fase 7:
harvest → tick → query, update in-place, tombstone→stale, idempotência.
"""
import json

from neurata.cli import main


def _write_skill(base, dirname, name, description, body):
    d = base / dirname
    d.mkdir()
    text = (f"---\nname: {name}\ndescription: {description}\n---\n{body}")
    (d / "SKILL.md").write_text(text, encoding="utf-8")


def test_harvest_tick_query_end_to_end(tmp_path, monkeypatch, capsys):
    home_dir = tmp_path / "home"
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setenv("NEURATA_HOME", str(home_dir))
    monkeypatch.setenv("NEURATA_CLAUDE_SKILLS_DIR", str(skills_dir))

    _write_skill(skills_dir, "alpha", "alpha-skill",
                "faz coisas alfa muito especificas",
                "# Alpha\nCorpo original da skill alpha.\n")
    _write_skill(skills_dir, "beta", "beta-skill",
                "faz coisas beta",
                "# Beta\nCorpo original da skill beta.\n")

    rc = main(["reindex", "--json"])
    assert rc == 0
    capsys.readouterr()

    # 1. harvest fresco -> 2 itens no inbox
    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["harvested"] == 2
    assert out["result"]["updated"] == 0
    assert out["result"]["removed"] == 0
    assert len(list(home_dir.joinpath("inbox").glob("*.md"))) == 2

    # 2. tick -> 2 entries type:skill na library
    rc = main(["tick", "--json"])
    assert rc == 0
    capsys.readouterr()
    assert list(home_dir.joinpath("inbox").glob("*.md")) == []
    lib_files = list(home_dir.joinpath("library").glob("*.md"))
    assert len(lib_files) == 2

    # 3. query acha a skill pela description (facet type:skill embutida em q)
    rc = main(["query", "type:skill alfa muito especificas", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    titles = [c["title"] for c in out["result"]["results"]]
    assert "alpha-skill" in titles

    # re-harvest idempotente (sem mudanca -> inbox continua vazio)
    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["harvested"] == 0
    assert out["result"]["updated"] == 0
    assert out["result"]["removed"] == 0
    assert list(home_dir.joinpath("inbox").glob("*.md")) == []

    # 4. muda 1 SKILL.md -> harvest -> tick -> update in-place
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha-skill\ndescription: faz coisas alfa muito "
        "especificas\n---\n# Alpha\nCorpo MUDADO da skill alpha.\n",
        encoding="utf-8")
    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["updated"] == 1

    rc = main(["tick", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    capsys.readouterr()
    lib_files_after_update = sorted(
        home_dir.joinpath("library").glob("*.md"))
    assert len(lib_files_after_update) == 2
    assert list(home_dir.joinpath("inbox").glob("*.md")) == []
    alpha_body = None
    for f in lib_files_after_update:
        if "alpha" in f.name:
            alpha_body = f.read_text(encoding="utf-8")
    assert alpha_body is not None
    assert "Corpo MUDADO da skill alpha." in alpha_body

    # 5. remove 1 SKILL.md -> harvest -> tick -> entry vira stale
    import shutil
    shutil.rmtree(skills_dir / "beta")
    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["removed"] == 1

    rc = main(["tick", "--json"])
    assert rc == 0
    capsys.readouterr()
    assert list(home_dir.joinpath("inbox").glob("*.md")) == []
    beta_body = None
    for f in home_dir.joinpath("library").glob("*.md"):
        if "beta" in f.name:
            beta_body = f.read_text(encoding="utf-8")
    assert beta_body is not None
    assert "stale: true" in beta_body

    # 6. re-harvest idempotente (segundo run sem skill nova -> inbox vazio)
    rc = main(["harvest", "claude-code", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"]["harvested"] == 0
    assert out["result"]["updated"] == 0
    assert out["result"]["removed"] == 0
    assert list(home_dir.joinpath("inbox").glob("*.md")) == []
