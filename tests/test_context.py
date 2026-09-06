"""tests/test_context.py — captura de contexto da busca (v1.6).

A captura é best-effort por construção: contexto ausente significa
"viés desligado", nunca erro. Cada teste fixa um caminho da precedência
declarada no design (env > git > nada).
"""
import subprocess

from neurata.context import QueryContext, capture_query_context


def _ctx(**kw) -> dict:
    return capture_query_context(**kw)


def test_env_project_vence_tudo(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_PROJECT", "ProjetoA")
    monkeypatch.setenv("NEURATA_SESSION", "s-1")
    c = _ctx(cwd=tmp_path)
    assert (c.project, c.project_source, c.session) == ("ProjetoA", "env",
                                                        "s-1")


def test_env_em_branco_e_ausente(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_PROJECT", "   ")
    c = _ctx(cwd=tmp_path)
    assert c.project is None and c.project_source == "none"


def test_git_root_vira_basename_normalizado(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)

    def fake_run(cmd, **kw):
        class P:
            returncode = 0
            stdout = "C:\\repos\\MeuProjeto\n"

        assert "rev-parse" in cmd
        return P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    c = _ctx(cwd=tmp_path)
    assert (c.project, c.project_source) == ("MeuProjeto", "git")


def test_git_root_posix(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)

    def fake_run(cmd, **kw):
        class P:
            returncode = 0
            stdout = "/home/x/OutroProjeto\n"

        return P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _ctx(cwd=tmp_path).project == "OutroProjeto"


def test_git_ausente_e_best_effort(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)

    def explode(cmd, **kw):
        raise OSError("git não instalado")

    monkeypatch.setattr(subprocess, "run", explode)
    c = _ctx(cwd=tmp_path)
    assert c.project is None and c.project_source == "none"


def test_git_exit_diferente_de_zero(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)

    def fake_run(cmd, **kw):
        class P:
            returncode = 128
            stdout = ""

        return P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _ctx(cwd=tmp_path).project is None


def test_git_timeout_e_best_effort(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)

    def devagar(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 0.5)

    monkeypatch.setattr(subprocess, "run", devagar)
    assert _ctx(cwd=tmp_path).project is None


def test_session_precedencia(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_SESSION", "s-neurata")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s-claude")
    assert _ctx(cwd=tmp_path).session == "s-neurata"
    monkeypatch.delenv("NEURATA_SESSION")
    assert _ctx(cwd=tmp_path).session == "s-claude"
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID")
    assert _ctx(cwd=tmp_path).session is None


def test_session_em_branco_e_ausente(tmp_path, monkeypatch):
    monkeypatch.setenv("NEURATA_SESSION", "  ")
    assert _ctx(cwd=tmp_path).session is None


def test_env_injetado_nao_toca_os_environ(tmp_path):
    c = _ctx(cwd=tmp_path, env={"NEURATA_PROJECT": "Injetado",
                                "NEURATA_SESSION": "s-inj"})
    assert (c.project, c.session, c.project_source) == ("Injetado", "s-inj",
                                                        "env")


def test_cwd_ausente_nao_levanta(tmp_path, monkeypatch):
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)
    morto = tmp_path / "nunca-existiu"
    c = _ctx(cwd=morto)  # git em dir inexistente falha -> best-effort
    assert isinstance(c, QueryContext)
    assert c.project_source in ("none", "git")
