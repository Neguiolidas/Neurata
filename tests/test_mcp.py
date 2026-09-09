"""tests/test_mcp.py — unidade do servidor MCP (enquadramento e despacho).

O fio completo, por subprocesso, vive em test_mcp_protocolo.py. Aqui é a
camada de baixo: quadros, códigos de erro e resolução de contexto.
"""
import io
import json

import pytest

from neurata.mcp import jsonrpc as j
from neurata.mcp.server import Servidor

# ---------------------------------------------------------------- quadros

def test_le_uma_linha_por_quadro():
    fonte = io.StringIO('{"a": 1}\n{"b": 2}\n')
    assert list(j.read_frames(fonte)) == ['{"a": 1}', '{"b": 2}']


def test_linha_em_branco_e_ignorada():
    """Linha vazia não é quadro. Sem isso, um `\\n` extra do cliente vira
    PARSE_ERROR e polui o canal com erro que não é erro."""
    fonte = io.StringIO('{"a": 1}\n\n   \n{"b": 2}\n')
    assert list(j.read_frames(fonte)) == ['{"a": 1}', '{"b": 2}']


def test_quadro_sem_newline_final_ainda_conta():
    fonte = io.StringIO('{"a": 1}')
    assert list(j.read_frames(fonte)) == ['{"a": 1}']


def test_quadro_gigante_vira_sentinela_e_nao_estoura_memoria():
    """O teto existe para um cliente doente não fazer o servidor alocar a
    linha inteira. O quadro seguinte tem que continuar sendo lido: descartar
    o excesso não pode descartar a sessão."""
    gigante = "x" * (j.DEFAULT_MAX_FRAME_BYTES + 10)
    fonte = io.StringIO(gigante + "\n" + '{"ok": 1}' + "\n")
    lidos = list(j.read_frames(fonte))
    assert lidos[0] is j.OVERSIZE
    assert lidos[1] == '{"ok": 1}'


def test_codigos_de_erro_sao_os_do_jsonrpc():
    assert (j.PARSE_ERROR, j.INVALID_REQUEST, j.METHOD_NOT_FOUND,
            j.INVALID_PARAMS, j.INTERNAL_ERROR) == (
                -32700, -32600, -32601, -32602, -32603)


def test_make_error_tem_forma_de_envelope():
    e = j.make_error(7, j.INVALID_PARAMS, "faltou x")
    assert e["jsonrpc"] == "2.0" and e["id"] == 7
    assert e["error"] == {"code": -32602, "message": "faltou x"}
    assert "result" not in e


# ------------------------------------------------------------- handshake

def _srv(tmp_path):
    return Servidor(home_root=tmp_path)


def test_initialize_ecoa_versao_pedida(tmp_path):
    s = _srv(tmp_path)
    for v in j.SUPPORTED_PROTOCOLS:
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": v}})
        assert r["result"]["protocolVersion"] == v


def test_initialize_com_versao_desconhecida_cai_na_ultima(tmp_path):
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": "1999-01-01"}})
    assert r["result"]["protocolVersion"] == j.SUPPORTED_PROTOCOLS[-1]


def test_chamada_antes_de_initialize_nao_derruba(tmp_path):
    """Ciclo de vida tolerante: cliente que esquece o handshake merece uma
    resposta, não um servidor morto."""
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert "result" in r


def test_notificacao_nao_gera_resposta(tmp_path):
    """Quadro sem `id` é notificação: responder a uma é violar o JSON-RPC."""
    s = _srv(tmp_path)
    assert s.handle({"jsonrpc": "2.0",
                     "method": "notifications/initialized"}) is None


# ----------------------------------------------------------------- tools

def test_tools_list_traz_as_quatro(tmp_path):
    s = _srv(tmp_path)
    nomes = {t["name"] for t in s.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]}
    assert nomes == {"neurata_query", "neurata_deposit", "neurata_expand",
                     "neurata_shelf"}


def test_todo_schema_fecha_a_porta_para_argumento_extra(tmp_path):
    """`additionalProperties: false` é o que torna a recusa do `restore` uma
    propriedade verificável em vez de uma omissão."""
    s = _srv(tmp_path)
    for t in s.handle({"jsonrpc": "2.0", "id": 1,
                       "method": "tools/list"})["result"]["tools"]:
        esq = t["inputSchema"]
        assert esq["type"] == "object"
        assert esq["additionalProperties"] is False, t["name"]
        assert "properties" in esq and "required" in esq


def test_expand_nao_expoe_restore(tmp_path):
    s = _srv(tmp_path)
    tools = s.handle({"jsonrpc": "2.0", "id": 1,
                      "method": "tools/list"})["result"]["tools"]
    exp = next(t for t in tools if t["name"] == "neurata_expand")
    assert "restore" not in exp["inputSchema"]["properties"]


def test_metodo_desconhecido(tmp_path):
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "nao/existe"})
    assert r["error"]["code"] == j.METHOD_NOT_FOUND


def test_tool_inexistente(tmp_path):
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_voar", "arguments": {}}})
    assert r["error"]["code"] == j.METHOD_NOT_FOUND


def test_argumento_obrigatorio_ausente(tmp_path):
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_query", "arguments": {}}})
    assert r["error"]["code"] == j.INVALID_PARAMS


def test_argumento_desconhecido_e_recusado(tmp_path):
    """O caso que motivou o `additionalProperties: false`: `restore` passando
    despercebido faria a superfície de leitura escrever."""
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_expand",
                             "arguments": {"ref": "x", "restore": True}}})
    assert r["error"]["code"] == j.INVALID_PARAMS
    assert "restore" in r["error"]["message"]


# --------------------------------------------------------------- contexto

def test_sem_nada_o_projeto_e_none_e_nunca_o_cwd(tmp_path, monkeypatch):
    """O cwd do servidor não é o do usuário. Derivar `project` dele enviesaria
    todo ranking com o projeto errado, calado."""
    monkeypatch.delenv("NEURATA_PROJECT", raising=False)
    s = _srv(tmp_path)
    ctx = s.contexto({})
    assert ctx.project is None
    assert ctx.project_source == "none"


def test_argumento_explicito_vence(tmp_path):
    s = _srv(tmp_path)
    s.roots = ["file:///c:/tmp/DoRoots"]
    ctx = s.contexto({"project": "DoArgumento"})
    assert (ctx.project, ctx.project_source) == ("DoArgumento", "arg")


def test_roots_preenche_quando_nao_ha_argumento(tmp_path):
    s = _srv(tmp_path)
    s.roots = ["file:///c:/Users/x/code/MeuRepo"]
    ctx = s.contexto({})
    assert (ctx.project, ctx.project_source) == ("MeuRepo", "roots")


def test_roots_vazio_equivale_a_ausencia(tmp_path):
    s = _srv(tmp_path)
    s.roots = []
    assert s.contexto({}).project_source == "none"


def test_roots_usa_a_primeira_raiz(tmp_path):
    s = _srv(tmp_path)
    s.roots = ["file:///c:/a/Primeiro", "file:///c:/b/Segundo"]
    assert s.contexto({}).project == "Primeiro"


def test_list_changed_invalida_o_cache(tmp_path):
    s = _srv(tmp_path)
    s.roots = ["file:///c:/a/Antigo"]
    assert s.handle({"jsonrpc": "2.0",
                     "method": "notifications/roots/list_changed"}) is None
    assert s.roots is None
    assert s.contexto({}).project_source == "none"


def test_pedido_de_roots_sai_uma_vez_apos_initialize(tmp_path):
    """Não bloqueante de propósito: esperar a resposta dentro de uma chamada
    de ferramenta seria ler o stdin no meio de outra leitura do stdin."""
    s = _srv(tmp_path)
    assert s.pendentes() == []
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": j.SUPPORTED_PROTOCOLS[-1],
                         "capabilities": {"roots": {"listChanged": True}}}})
    saida = s.pendentes()
    assert [p["method"] for p in saida] == ["roots/list"]
    assert s.pendentes() == [], "o pedido não pode sair duas vezes"


def test_sem_capacidade_roots_o_servidor_nao_pede(tmp_path):
    s = _srv(tmp_path)
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": j.SUPPORTED_PROTOCOLS[-1],
                         "capabilities": {}}})
    assert s.pendentes() == []


def test_resposta_de_roots_e_casada_pelo_id(tmp_path):
    s = _srv(tmp_path)
    s.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": j.SUPPORTED_PROTOCOLS[-1],
                         "capabilities": {"roots": {"listChanged": True}}}})
    pedido = s.pendentes()[0]
    s.handle({"jsonrpc": "2.0", "id": pedido["id"],
              "result": {"roots": [{"uri": "file:///c:/x/Alvo",
                                    "name": "Alvo"}]}})
    assert s.contexto({}).project == "Alvo"


def test_resposta_de_id_desconhecido_e_ignorada_sem_quebrar(tmp_path):
    s = _srv(tmp_path)
    assert s.handle({"jsonrpc": "2.0", "id": 99999,
                     "result": {"roots": []}}) is None


# ------------------------------------------------------------- resultado

def test_sucesso_vem_embrulhado_em_content_de_texto(tmp_path):
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_shelf", "arguments": {}}})
    conteudo = r["result"]["content"]
    assert conteudo[0]["type"] == "text"
    assert isinstance(json.loads(conteudo[0]["text"]), dict)


def test_erro_de_dominio_volta_como_sucesso_com_ok_falso(tmp_path):
    """Erro de domínio não é erro de protocolo. Devolver -32603 aqui faria o
    servidor parecer quebrado quando quem errou foi a query."""
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_query",
                             "arguments": {"q": ""}}})
    assert "error" not in r
    carga = json.loads(r["result"]["content"][0]["text"])
    assert carga["ok"] is False and carga["error"]


def test_lock_ocupado_vira_dominio_com_retryable(tmp_path, monkeypatch):
    from neurata.indexdb import LockHeldError
    from neurata.mcp import server as mod

    def explode(*a, **k):
        raise LockHeldError("lock ativo")

    monkeypatch.setattr(mod, "_shelf_inventory", explode)
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_shelf", "arguments": {}}})
    assert "error" not in r
    carga = json.loads(r["result"]["content"][0]["text"])
    assert carga["ok"] is False and carga["retryable"] is True


def test_excecao_inesperada_nao_vaza_traceback(tmp_path, monkeypatch):
    from neurata.mcp import server as mod

    def explode(*a, **k):
        raise RuntimeError("segredo interno /home/alguem/x")

    monkeypatch.setattr(mod, "_shelf_inventory", explode)
    s = _srv(tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_shelf", "arguments": {}}})
    assert r["error"]["code"] == j.INTERNAL_ERROR
    assert "segredo interno" not in json.dumps(r)


@pytest.mark.parametrize("quadro", [
    [], "texto", 3, None,
])
def test_quadro_que_nao_e_objeto_vira_invalid_request(tmp_path, quadro):
    s = _srv(tmp_path)
    r = s.handle(quadro)
    assert r["error"]["code"] == j.INVALID_REQUEST


def test_deposit_numa_home_virgem_funciona(tmp_path):
    """Regressão de um bug meu: o servidor não chamava `home.init()`, então o
    depósito gravava numa árvore de pastas que não existia e devolvia
    `internal error` — mudo, porque o traceback também estava escondido do
    stderr. As tools de leitura toleravam a home vazia e não pegaram isso."""
    s = Servidor(home_root=tmp_path / "virgem")
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_deposit",
                             "arguments": {"content": "conteudo cru",
                                           "title": "Titulo"}}})
    assert "error" not in r, r
    carga = json.loads(r["result"]["content"][0]["text"])
    assert carga["ok"] is True and carga["action"] == "created"
    assert (tmp_path / "virgem" / "inbox").is_dir()
    assert (tmp_path / "virgem" / "config.json").exists()


def test_erro_inesperado_vai_para_o_stderr(tmp_path, monkeypatch, capsys):
    """O cliente recebe genérico; o operador precisa do traceback. Esconder de
    todo mundo foi o que transformou o bug acima numa rodada de depuração."""
    from neurata.mcp import server as mod

    def explode(*a, **k):
        raise RuntimeError("estouro proposital")

    monkeypatch.setattr(mod, "_shelf_inventory", explode)
    monkeypatch.setattr(mod, "_DOMINIO", ())
    s = Servidor(home_root=tmp_path)
    r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "neurata_shelf", "arguments": {}}})
    assert r["error"]["code"] == j.INTERNAL_ERROR
    assert "estouro proposital" not in json.dumps(r)
    assert "estouro proposital" in capsys.readouterr().err
