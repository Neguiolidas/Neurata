"""tests/test_mcp_protocolo.py — Suíte de caixa preta no fio do servidor MCP Neurata.

Testa o contrato JSON-RPC 2.0 sobre stdin/stdout via subprocesso real
(`python -m neurata.mcp.server`). Nenhuma entranha de neurata.mcp é importada
aqui: o teste valida exclusivamente a experiência observável pelo cliente MCP.
"""
from __future__ import annotations

import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from neurata.home import NeurataHome

SUPPORTED_PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]
EXPECTED_TOOLS = {"neurata_query", "neurata_deposit", "neurata_expand", "neurata_shelf"}


# Generoso de proposito. Na suite cheia, 3 s mediam a CARGA DA MAQUINA e
# nao o contrato: os casos 08 e 09 caiam com o servidor vivo (exit code
# None) e stderr vazio, so por ainda nao ter respondido. Um servidor que
# de fato travasse continua sendo pego aqui — apenas demora mais para
# falhar, o que e o lado certo do erro.
TIMEOUT_PADRAO = 30.0


class MCPProcessDriver:
    """Controlador de subprocesso MCP para testes de protocolo de caixa preta."""

    def __init__(
        self,
        home_path: Path,
        cwd: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.home_path = home_path
        env = os.environ.copy()
        env["NEURATA_HOME"] = str(home_path)
        env["PYTHONIOENCODING"] = "utf-8"

        neurata_repo = str(Path(__file__).resolve().parent.parent)
        current_pp = env.get("PYTHONPATH", "")
        if current_pp:
            env["PYTHONPATH"] = f"{neurata_repo}{os.pathsep}{current_pp}"
        else:
            env["PYTHONPATH"] = neurata_repo

        if extra_env:
            env.update(extra_env)

        self.proc = subprocess.Popen(
            [sys.executable, "-m", "neurata.mcp.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(cwd or home_path),
            env=env,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )

        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.stderr_lines: list[str] = []

        self._t_stdout = threading.Thread(target=self._reader_stdout, daemon=True)
        self._t_stderr = threading.Thread(target=self._reader_stderr, daemon=True)
        self._t_stdout.start()
        self._t_stderr.start()

    def _reader_stdout(self) -> None:
        if self.proc.stdout is None:
            return
        with contextlib.suppress(ValueError, OSError):
            for line in self.proc.stdout:
                self.stdout_queue.put(line)

    def _reader_stderr(self) -> None:
        if self.proc.stderr is None:
            return
        with contextlib.suppress(ValueError, OSError):
            for line in self.proc.stderr:
                self.stderr_lines.append(line)

    def send_raw(self, data: str) -> None:
        """Envia string bruta para stdin garantindo terminação em newline."""
        if not self.proc.stdin:
            raise RuntimeError("stdin fechado")
        if not data.endswith("\n"):
            data += "\n"
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send_json(self, payload: dict[str, Any]) -> None:
        """Serializa e envia mensagem JSON-RPC."""
        self.send_raw(json.dumps(payload, ensure_ascii=False))

    def read_raw(self, timeout: float = TIMEOUT_PADRAO) -> str:
        """Lê a próxima linha emitida no stdout pelo subprocesso."""
        try:
            return self.stdout_queue.get(timeout=timeout)
        except queue.Empty as err:
            code = self.proc.poll()
            err_output = "".join(self.stderr_lines)
            raise TimeoutError(
                f"Timeout ({timeout}s) aguardando saída do servidor. "
                f"Exit code: {code}. Stderr: {err_output}"
            ) from err

    def read_json(self, timeout: float = TIMEOUT_PADRAO) -> dict[str, Any]:
        """Lê a próxima linha do stdout e decodifica como objeto JSON."""
        line = self.read_raw(timeout=timeout)
        try:
            return json.loads(line)
        except json.JSONDecodeError as err:
            raise AssertionError(
                f"Servidor emitiu linha que não é JSON válido no stdout: {line!r}"
            ) from err

    def close(self) -> None:
        """Encerra o processo de forma limpa garantindo liberação de recursos."""
        if self.proc.stdin:
            with contextlib.suppress(OSError):
                self.proc.stdin.close()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            with contextlib.suppress(OSError):
                self.proc.kill()


@pytest.fixture
def mcp_home(tmp_path: Path) -> Path:
    """Inicializa um NeurataHome temporário e isolado para o teste."""
    home = NeurataHome(tmp_path)
    home.init()
    (home.library / "nota_inicial.md").write_text(
        "---\nid: 01INICIAL00000000000000001\ntitle: Nota Inicial\n---\nConteúdo para busca inicial.\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Caso 1: Handshake initialize ecoa versões suportadas e fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("versao", SUPPORTED_PROTOCOLS)
def test_01_handshake_initialize_ecoa_versoes_suportadas(
    mcp_home: Path, versao: str
) -> None:
    """O handshake ecoa fielmente qualquer uma das 3 versões declaradas na spec."""
    driver = MCPProcessDriver(mcp_home)
    try:
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": versao, "capabilities": {}},
        })
        res = driver.read_json()
        assert res.get("id") == 1
        assert "result" in res
        assert res["result"].get("protocolVersion") == versao
        assert res["result"].get("capabilities") == {"tools": {"listChanged": False}}
        assert res["result"].get("serverInfo", {}).get("name") == "neurata"
    finally:
        driver.close()


def test_01_handshake_initialize_fallback_para_versao_desconhecida(
    mcp_home: Path,
) -> None:
    """Versão de protocolo desconhecida pelo servidor cai na última suportada."""
    driver = MCPProcessDriver(mcp_home)
    try:
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 10,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01", "capabilities": {}},
        })
        res = driver.read_json()
        assert res.get("id") == 10
        assert res["result"].get("protocolVersion") == SUPPORTED_PROTOCOLS[-1]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 2: Chamada antes de initialize não derruba o servidor
# ---------------------------------------------------------------------------

def test_02_chamada_antes_de_initialize_nao_derruba(mcp_home: Path) -> None:
    """Ciclo de vida tolerante: cliente que esquece o handshake não mata o servidor."""
    driver = MCPProcessDriver(mcp_home)
    try:
        # Chamada de tools/list antes de qualquer initialize
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
        })
        res1 = driver.read_json()
        assert res1.get("id") == 1
        assert "result" in res1
        assert "tools" in res1["result"]

        # O servidor segue vivo e atende o initialize subsequentemente
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {"protocolVersion": SUPPORTED_PROTOCOLS[-1]},
        })
        res2 = driver.read_json()
        assert res2.get("id") == 2
        assert res2["result"].get("protocolVersion") == SUPPORTED_PROTOCOLS[-1]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 3: tools/list traz exatamente 4 ferramentas e esquemas estritos
# ---------------------------------------------------------------------------

def test_03_tools_list_quatro_ferramentas_e_esquemas_estritos(
    mcp_home: Path,
) -> None:
    """tools/list expõe 4 ferramentas com additionalProperties: false em todas."""
    driver = MCPProcessDriver(mcp_home)
    try:
        driver.send_json({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        res = driver.read_json()
        tools = res["result"]["tools"]
        nomes = {t["name"] for t in tools}
        assert nomes == EXPECTED_TOOLS

        for t in tools:
            schema = t.get("inputSchema", {})
            assert schema.get("type") == "object"
            assert schema.get("additionalProperties") is False, (
                f"Ferramenta {t['name']} deve ter additionalProperties: false"
            )
            assert isinstance(schema.get("properties"), dict)
            assert isinstance(schema.get("required"), list)

        # expand não expõe restore no schema
        expand_tool = next(t for t in tools if t["name"] == "neurata_expand")
        assert "restore" not in expand_tool["inputSchema"]["properties"]
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 4: JSON inválido numa linha devolve -32700 e servidor segue
# ---------------------------------------------------------------------------

def test_04_json_invalido_devolve_parse_error_e_mantem_sessao(
    mcp_home: Path,
) -> None:
    """Linha inválida/malformada devolve -32700 e o servidor continua atendendo."""
    driver = MCPProcessDriver(mcp_home)
    try:
        driver.send_raw("isto com certeza nao e um json\n")
        res_err = driver.read_json()
        assert res_err.get("error", {}).get("code") == -32700

        # O servidor segue ativo e processa a requisição válida seguinte
        driver.send_json({"jsonrpc": "2.0", "id": 77, "method": "tools/list"})
        res_ok = driver.read_json()
        assert res_ok.get("id") == 77
        assert "tools" in res_ok.get("result", {})
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 5: Quadro acima de 1 MiB devolve -32600
# ---------------------------------------------------------------------------

def test_05_quadro_acima_de_1_mib_devolve_invalid_request(
    mcp_home: Path,
) -> None:
    """Quadro que excede o teto de 1 MiB é drenado e devolve erro -32600."""
    driver = MCPProcessDriver(mcp_home)
    try:
        # Gera quadro ligeiramente maior que 1 MiB (1024*1024 + 1024 bytes)
        carga_grande = "a" * (1024 * 1024 + 1024)
        msg_grande = json.dumps({
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "neurata_query", "arguments": {"q": carga_grande}},
        })
        driver.send_raw(msg_grande + "\n")

        res = driver.read_json()
        assert res.get("error", {}).get("code") == -32600

        # Servidor não morreu por OOM ou crash e responde a requisições seguintes
        driver.send_json({"jsonrpc": "2.0", "id": 100, "method": "tools/list"})
        res2 = driver.read_json()
        assert res2.get("id") == 100
        assert "tools" in res2.get("result", {})
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 6: Tool inexistente (-32601) e argumento obrigatório ausente (-32602)
# ---------------------------------------------------------------------------

def test_06_erros_metodo_inexistente_e_parametro_obrigatorio(
    mcp_home: Path,
) -> None:
    """Valida códigos de erro JSON-RPC canônicos para dispatch de ferramentas."""
    driver = MCPProcessDriver(mcp_home)
    try:
        # Tool inexistente -> METHOD_NOT_FOUND (-32601)
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "neurata_ferramenta_inexistente", "arguments": {}},
        })
        res1 = driver.read_json()
        assert res1.get("id") == 1
        assert res1.get("error", {}).get("code") == -32601

        # Argumento obrigatório ausente (ex: 'q' em neurata_query) -> INVALID_PARAMS (-32602)
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "neurata_query", "arguments": {}},
        })
        res2 = driver.read_json()
        assert res2.get("id") == 2
        assert res2.get("error", {}).get("code") == -32602
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 7: neurata_expand recusa restore com -32602
# ---------------------------------------------------------------------------

def test_07_neurata_expand_recusa_restore_com_invalid_params(
    mcp_home: Path,
) -> None:
    """restore: true em neurata_expand é rejeitado com -32602 por additionalProperties."""
    driver = MCPProcessDriver(mcp_home)
    try:
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "neurata_expand",
                "arguments": {"ref": "alvo-inexistente", "restore": True},
            },
        })
        res = driver.read_json()
        assert res.get("id") == 1
        assert res.get("error", {}).get("code") == -32602
        assert "restore" in res.get("error", {}).get("message", "")
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 8: Higiene do stdout (D1)
# ---------------------------------------------------------------------------

def test_08_higiene_stdout_print_de_dominio_vaza_para_stderr(
    mcp_home: Path, tmp_path: Path
) -> None:
    """Print acidental de domínio não corrompe o stdout JSON-RPC e vaza para stderr."""
    # Cria módulo sitecustomize temporário para injetar print na camada de domínio
    patch_dir = tmp_path / "patch_site"
    patch_dir.mkdir(parents=True, exist_ok=True)
    patch_file = patch_dir / "sitecustomize.py"
    sentinela = "POLLUTION_TEST_STDOUT_SPURIOUS_SENTINEL_789"

    patch_code = f"""
import sys
try:
    import neurata.shelf as s
    _real_inventory = s.inventory
    def _noisy_inventory(*args, **kwargs):
        print({sentinela!r})
        return _real_inventory(*args, **kwargs)
    s.inventory = _noisy_inventory
except Exception:
    pass
"""
    patch_file.write_text(patch_code, encoding="utf-8")

    current_pp = os.environ.get("PYTHONPATH", "")
    new_pp = str(patch_dir) if not current_pp else f"{patch_dir}{os.pathsep}{current_pp}"

    driver = MCPProcessDriver(mcp_home, extra_env={"PYTHONPATH": new_pp})
    try:
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "neurata_shelf", "arguments": {}},
        })
        # O stdout DEVE conter JSON válido e decodificável (não corrompido)
        res = driver.read_json(timeout=TIMEOUT_PADRAO)
        assert res.get("id") == 1
        assert "result" in res

        # Dá tempo hábil para a thread de stderr drenar o buffer
        stderr_completo = "".join(driver.stderr_lines)
        assert sentinela in stderr_completo, (
            "A mensagem impressa no domínio deve vazar no stderr, comprovando o desvio D1"
        )
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 9: Contexto — cwd do processo nunca vaza para o ranking (D2)
# ---------------------------------------------------------------------------

def test_09_contexto_cwd_do_processo_nunca_vaza_para_o_ranking(
    mcp_home: Path, tmp_path: Path
) -> None:
    """Subprocesso rodando dentro de um git não vaza o nome desse git como projeto."""
    git_repo = tmp_path / "repositorio_secreto_cwd"
    git_repo.mkdir(parents=True, exist_ok=True)

    # Inicializa repositório git real
    subprocess.run(
        ["git", "init"],
        cwd=str(git_repo),
        check=True,
        capture_output=True,
    )

    # Roda o servidor com cwd dentro do git_repo
    driver = MCPProcessDriver(mcp_home, cwd=git_repo)
    try:
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": SUPPORTED_PROTOCOLS[-1], "capabilities": {}},
        })
        driver.read_json()

        # Query sem project e sem roots respondido
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "neurata_query", "arguments": {"q": "termo"}},
        })
        res = driver.read_json()
        assert res.get("id") == 2
        content_text = res["result"]["content"][0]["text"]
        payload = json.loads(content_text)

        ctx = payload.get("context", {})
        # O nome do repositório onde o servidor roda NÃO pode vazar
        assert ctx.get("project") != "repositorio_secreto_cwd"
        assert ctx.get("project") is None
        assert ctx.get("source") == "none"
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 10: Resolução de roots (D2)
# ---------------------------------------------------------------------------

def test_10_contexto_roots_list_respondido_vira_source_roots(
    mcp_home: Path,
) -> None:
    """Resposta a roots/list define project com a última pasta da URI e source='roots'."""
    driver = MCPProcessDriver(mcp_home)
    try:
        # Handshake declarando suporte a roots
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": SUPPORTED_PROTOCOLS[-1],
                "capabilities": {"roots": {"listChanged": True}},
            },
        })
        res_init = driver.read_json()
        assert res_init.get("id") == 1

        # Servidor emite roots/list para o cliente
        req_roots = driver.read_json()
        assert req_roots.get("method") == "roots/list"
        req_id = req_roots["id"]

        # Cliente responde ao pedido roots/list
        driver.send_json({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "roots": [{"uri": "file:///c:/projetos/MeuProjetoAlvo", "name": "MeuProjetoAlvo"}]
            },
        })

        # Executa query sem project explícito
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "neurata_query", "arguments": {"q": "termo"}},
        })
        res_query = driver.read_json()
        assert res_query.get("id") == 2
        payload = json.loads(res_query["result"]["content"][0]["text"])

        ctx = payload.get("context", {})
        assert ctx.get("project") == "MeuProjetoAlvo"
        assert ctx.get("source") == "roots"
    finally:
        driver.close()


# ---------------------------------------------------------------------------
# Caso 11: Não-bloqueio de roots (D2)
# ---------------------------------------------------------------------------

def test_11_roots_nao_bloqueante_tool_responde_sem_esperar_cliente(
    mcp_home: Path,
) -> None:
    """Chamar ferramenta antes de responder roots/list responde imediatamente sem travar."""
    driver = MCPProcessDriver(mcp_home)
    try:
        # Handshake declarando capacidade de roots
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": SUPPORTED_PROTOCOLS[-1],
                "capabilities": {"roots": {"listChanged": True}},
            },
        })
        res_init = driver.read_json()
        assert res_init.get("id") == 1

        # Servidor emite pedido roots/list
        req_roots = driver.read_json()
        assert req_roots.get("method") == "roots/list"

        # NÃO respondemos ao roots/list e chamamos uma tool imediatamente
        driver.send_json({
            "jsonrpc": "2.0",
            "id": 50,
            "method": "tools/call",
            "params": {"name": "neurata_shelf", "arguments": {}},
        })

        # O servidor DEVE responder à ferramenta sem timeout e sem esperar pelo roots/list
        res_tool = driver.read_json(timeout=TIMEOUT_PADRAO)
        assert res_tool.get("id") == 50
        assert "result" in res_tool
    finally:
        driver.close()
