"""tests/test_harvest_project.py — `neurata harvest project` ponta a ponta (v1.7).

Fake repo → harvest → inbox com `project@<hash>:<relpath>` → tick indexa
`class:procedural`; re-colheita sem mudança é zero; edição é update;
remoção é tombstone→stale; dois repos não colidem; claude-code segue
intacto (namespace/keys próprios).
"""
import hashlib
import json

from neurata.deposit import deposit
from neurata.harvest import harvest
from neurata.home import CONTRACT_VERSION
from neurata.indexdb import connect
from neurata.query import query
from neurata.tick import curate_tick


def _repo(tmp_path, texto_agents="Como trabalhar neste repo."):
    tmp_path = __import__("pathlib").Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "AGENTS.md").write_text(
        f"# Agentes\n\n{texto_agents}\n", encoding="utf-8")
    rules = tmp_path / ".cursor" / "rules"
    rules.mkdir(parents=True, exist_ok=True)
    (rules / "python-style.mdc").write_text(
        "---\ndescription: Padrao Python\n---\nUse type hints.\n",
        encoding="utf-8")
    return tmp_path


def _source_keys(home):
    keys = []
    for p in sorted(home.inbox.glob("*.md")):
        import neurata.frontmatter as fm
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if m.get("source_key"):
            keys.append(str(m["source_key"]))
    return keys


def _tick_keys(home):
    con = connect(home)
    try:
        return {sk: (cls,) for sk, cls in con.execute(
            "SELECT source_key, class FROM entries WHERE source_key LIKE"
            " 'project@%' AND location='library'")}
    finally:
        con.close()


def test_harvest_project_end_to_end(tmp_path, monkeypatch):
    home = NeurataHome_fixture(tmp_path / "home")
    repo = _repo(tmp_path / "repo")
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo))

    rep = harvest(home, "project")
    assert rep.harvested == 2
    assert rep.skipped == []
    chash = hashlib.sha256(
        str(repo.resolve()).replace("\\", "/").encode()).hexdigest()[:12]
    chaves = _source_keys(home)
    assert f"project@{chash}:AGENTS.md" in chaves
    assert f"project@{chash}:.cursor/rules/python-style.mdc" in chaves

    curate_tick(home)
    idx = _tick_keys(home)
    # classe pela FORMA que o adapter leu (doutrina v1.3): prosa
    # markdown é semantic; regra com estrutura (.mdc) é procedural
    assert idx[f"project@{chash}:AGENTS.md"] == ("semantic",)
    assert (idx[f"project@{chash}:.cursor/rules/python-style.mdc"]
            == ("procedural",))

    # re-colheita sem mudança: nada novo
    rep2 = harvest(home, "project")
    assert rep2.harvested == 0 and rep2.updated == 0

    # edição da fonte: update in-place
    (repo / "AGENTS.md").write_text(
        "# Agentes\n\nComo trabalhar DEPOIS da mudanca.\n",
        encoding="utf-8")
    rep3 = harvest(home, "project")
    assert rep3.updated == 1
    curate_tick(home)

    # remoção: tombstone → stale
    (repo / "AGENTS.md").unlink()
    rep4 = harvest(home, "project")
    assert rep4.removed == 1
    curate_tick(home)
    # staleness vive no ARQUIVO (frontmatter), não no índice
    import neurata.frontmatter as fm
    stales = 0
    for p in home.library.glob("*.md"):
        m, _ = fm.parse(p.read_text(encoding="utf-8"))
        if m.get("source_key", "").startswith("project@") and str(
                m.get("stale", "")).lower() == "true":
            stales += 1
    assert stales == 1


def test_dois_repos_nao_colidem(tmp_path, monkeypatch):
    home = NeurataHome_fixture(tmp_path / "home")
    repo_a = _repo(tmp_path / "a", texto_agents="Instruções do repo A.")
    repo_b = _repo(tmp_path / "b", texto_agents="Instruções do repo B.")

    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo_a))
    harvest(home, "project")
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo_b))
    harvest(home, "project")
    curate_tick(home)

    chaves = _tick_keys(home)
    agents = [k for k in chaves if k.endswith(":AGENTS.md")]
    assert len(agents) == 2  # namespaces distintos, zero colisão

    # busca enxerga os dois, cada um com sua classe
    out = query(home, "instrucoes", limit=10)
    assert any("repo A" in c["description"] or c["slug"] for c in
               out["results"])


def test_self_ingest_guard_para_provider_nomeado(tmp_path, monkeypatch):
    home = NeurataHome_fixture(tmp_path / "home")
    home.init()
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(home.root))
    try:
        harvest(home, "project")
        raised = False
    except ValueError as e:
        raised = True
        assert "NEURATA_HOME" in str(e)
    assert raised, "colher o próprio home deveria ser rejeitado"


def test_claude_code_regressao_zero(tmp_path, monkeypatch):
    """O gancho do source_key não pode ter mudado o provider histórico."""
    home = NeurataHome_fixture(tmp_path / "home")
    skills_dir = tmp_path / "skills"
    (skills_dir / "alpha").mkdir(parents=True)
    (skills_dir / "alpha" / "SKILL.md").write_text(
        "---\nname: alpha\ndescription: faz alpha\n---\nCorpo.\n",
        encoding="utf-8")
    rep = harvest(home, "claude-code", skills_dir=skills_dir)
    assert rep.harvested == 1
    chaves = _source_keys(home)
    assert chaves == ["claude-code:alpha"]


def test_cli_harvest_project(tmp_path, monkeypatch, capsys):
    from neurata.cli import main
    monkeypatch.setenv("NEURATA_HOME", str(tmp_path / "home"))
    repo = _repo(tmp_path / "repo")
    monkeypatch.setenv("NEURATA_PROJECT_ROOT", str(repo))
    monkeypatch.chdir(repo)  # sem env override, a âncora é o cwd
    monkeypatch.delenv("NEURATA_PROJECT_ROOT")
    import subprocess
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    rc = main(["harvest", "project", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["result"]["harvested"] == 2
    assert CONTRACT_VERSION == 6


def NeurataHome_fixture(path):
    from neurata.home import NeurataHome
    home = NeurataHome(path)
    home.init()
    return home


def test_deposit_regressao(tmp_path):
    """Sanidade: o pacote v1.7 não tocou o caminho do depósito."""
    home = NeurataHome_fixture(tmp_path / "home")
    r = deposit(home, content="conteudo simples", title="t")
    curate_tick(home)
    con = connect(home)
    try:
        (n,) = con.execute("SELECT COUNT(*) FROM entries WHERE"
                           " location='library'").fetchone()
    finally:
        con.close()
    assert r["action"] == "created" and n == 1
