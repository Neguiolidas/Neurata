"""tests/test_envelope.py"""
import subprocess
from unittest.mock import patch

import pytest

from neurata.envelope import _git_context, _normalize_agent, capture


def test_always_present_fields(tmp_path, sem_procedencia):
    env = capture(cwd=tmp_path)
    assert env["host"]
    assert env["cwd"] == str(tmp_path)
    assert env["ts"].endswith("+00:00")
    assert env["origin"] == "manual"
    assert "agent" not in env and "session" not in env


def test_optional_agent_session(tmp_path):
    env = capture(agent="claude", session="s1", cwd=tmp_path)
    assert env["agent"] == "claude"
    assert env["session"] == "s1"


def test_git_context_inside_repo(tmp_path):
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)],
                   check=True, capture_output=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "f"],
                   check=True, capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-m", "init"],
                   check=True, capture_output=True)
    env = capture(cwd=tmp_path)
    assert env["git"]["branch"] == "main"
    assert env["git"]["root"] == str(tmp_path.resolve())
    assert len(env["git"]["commit"]) == 12


def test_no_git_outside_repo(tmp_path):
    env = capture(cwd=tmp_path)
    assert "git" not in env


def test_empty_agent_session_counts_as_absence(tmp_path, sem_procedencia):
    """v1.2 (D1) revoga o contrato antigo, em que `agent=""` era preservado
    como distinto de "não informado". Procedência vazia é `None`, nunca
    string vazia: `missing:agent` é `agent IS NULL` (query.py:146), então um
    card com `agent=''` não seria "missing" nem teria agente — um buraco."""
    env = capture(agent="", session="", cwd=tmp_path)
    assert "agent" not in env and "session" not in env


def test_capture_survives_deleted_cwd(tmp_path, monkeypatch):
    """Path.cwd() levantando OSError (cwd removido sob o processo) não
    derruba capture() — cai pra um cwd de fallback."""
    with patch("neurata.envelope.Path.cwd", side_effect=OSError("gone")):
        env = capture()
    assert env["cwd"]


def test_capture_survives_gethostname_failure(tmp_path):
    with patch("neurata.envelope.socket.gethostname",
               side_effect=OSError("no host")):
        env = capture(cwd=tmp_path)
    assert env["host"] == "unknown"


def test_git_context_short_output_no_commit_yet(tmp_path):
    """rev-parse com rc=0 mas só 1 linha (repo sem commit em alguns gits)
    ainda produz 'root' sem KeyError/IndexError."""
    fake = subprocess.CompletedProcess(args=[], returncode=0,
                                        stdout=str(tmp_path) + "\n")
    with patch("neurata.envelope.subprocess.run", return_value=fake):
        ctx = _git_context(tmp_path)
    assert ctx == {"root": str(tmp_path.resolve())}


_HOST_VARS = ("NEURATA_AGENT", "NEURATA_SESSION", "AI_AGENT",
              "CLAUDE_CODE_SESSION_ID")


@pytest.fixture
def sem_procedencia(monkeypatch):
    """Env limpa: os testes de procedência não podem depender da env real
    do processo que roda a suíte (que hoje TEM AI_AGENT setado)."""
    for var in _HOST_VARS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_capture_reads_agent_and_session_from_host_env(sem_procedencia):
    sem_procedencia.setenv("AI_AGENT", "claude-code_2-1-229_agent")
    sem_procedencia.setenv("CLAUDE_CODE_SESSION_ID", "00000000-0000-4000-8000-000000000001")
    env = capture()
    assert env["agent"] == "claude-code"
    assert env["session"] == "00000000-0000-4000-8000-000000000001"


def test_capture_neurata_vars_beat_host_vars(sem_procedencia):
    sem_procedencia.setenv("NEURATA_AGENT", "hermes")
    sem_procedencia.setenv("AI_AGENT", "claude-code_2-1-229_agent")
    sem_procedencia.setenv("NEURATA_SESSION", "s-neurata")
    sem_procedencia.setenv("CLAUDE_CODE_SESSION_ID", "s-host")
    env = capture()
    assert (env["agent"], env["session"]) == ("hermes", "s-neurata")


def test_capture_explicit_argument_beats_env(sem_procedencia):
    sem_procedencia.setenv("NEURATA_AGENT", "do-env")
    sem_procedencia.setenv("NEURATA_SESSION", "sessao-env")
    env = capture(agent="hermes", session="s-1")
    assert (env["agent"], env["session"]) == ("hermes", "s-1")


def test_capture_omits_provenance_when_env_absent(sem_procedencia):
    env = capture()
    assert "agent" not in env and "session" not in env


def test_capture_treats_blank_as_absence(sem_procedencia):
    sem_procedencia.setenv("AI_AGENT", "   ")
    sem_procedencia.setenv("CLAUDE_CODE_SESSION_ID", "")
    env = capture(agent="  ")
    assert "agent" not in env and "session" not in env


def test_capture_does_not_normalize_neurata_agent(sem_procedencia):
    """`NEURATA_AGENT` é override do usuário: vale literal. Só `AI_AGENT`
    passa pelo normalizador (D1)."""
    sem_procedencia.setenv("NEURATA_AGENT", "meu_agent")
    assert capture()["agent"] == "meu_agent"


@pytest.mark.parametrize("raw, esperado", [
    ("claude-code_2-1-229_agent", "claude-code"),
    ("claude-code_2-2-0_agent", "claude-code"),
    ("claude-code", "claude-code"),
    ("hermes_agent", "hermes"),
    ("mcp_server_v1.4.2_agent", "mcp_server"),
    ("  claude-code_2-1-229_agent  ", "claude-code"),
    ("agent", "agent"),      # sobraria vazio → devolve o cru
    ("2-1-229", "2-1-229"),  # idem: normalizar não pode apagar o dado
])
def test_normalize_agent(raw, esperado):
    assert _normalize_agent(raw) == esperado
