"""neurata/mcp/server.py — servidor MCP da Neurata: quatro ferramentas.

Superfície, não capacidade. Cada ferramenta chama a função PURA
correspondente, nunca `cli.main` nem os auxiliares de impressão da CLI.

TRÊS DECISÕES QUE EXPLICAM O QUE PARECE ESTRANHO AQUI
-----------------------------------------------------
1. O canal do protocolo NÃO é `sys.stdout`. Na partida duplicamos o descritor
   1, apontamos o 1 para o stderr e trocamos `sys.stdout` por `sys.stderr`. A
   especificação do transporte stdio tem um MUST NOT contra escrever não-JSON
   no stdout; o Conscio cumpre isso por disciplina, pondo `file=sys.stderr` em
   cada `print`. Disciplina é promessa que todo contribuidor futuro precisa
   manter; duplicar o descritor transforma a promessa em propriedade. O caminho
   de import da Neurata está limpo hoje — medido, zero prints — mas isso é um
   fato sobre hoje.

2. O `project` NUNCA sai do cwd deste processo. O cwd do servidor não é o do
   usuário, e a Neurata deriva projeto do git do cwd: um servidor ingênuo
   enviesaria todo ranking com o projeto errado, calado. A ordem é argumento
   explícito, depois `roots` do cliente, depois nada — e `nada` se declara como
   `source: "none"`. De quebra, passar o contexto pronto pula o subprocesso de
   git de `_capture_if_worth`, que medimos em 96,5 ms dos ~140 ms de uma busca.

3. `roots/list` é pedido do servidor AO cliente, e este servidor é síncrono e
   de uma linha de execução só. Esperar a resposta dentro de uma chamada de
   ferramenta seria ler o stdin no meio de outra leitura do stdin: reentrância,
   e travamento dos dois lados. Então o pedido é enfileirado uma vez após o
   `initialize` e a resposta é casada pelo `id` no laço principal. Nenhuma
   ferramenta jamais espera pelo cliente.
"""
import contextlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from neurata import __version__
from neurata.context import QueryContext
from neurata.deposit import DepositError
from neurata.deposit import deposit as _deposit
from neurata.entryref import EntryAmbiguousError, EntryNotFoundError
from neurata.expand import ExpandError
from neurata.expand import expand as _expand
from neurata.home import CONTRACT_VERSION, NeurataHome
from neurata.indexdb import FTS5MissingError, IndexSchemaError, LockHeldError
from neurata.mcp import jsonrpc as j
from neurata.query import QueryError
from neurata.query import query as _query
from neurata.shelf import conflicts as _shelf_conflicts
from neurata.shelf import insights as _shelf_insights
from neurata.shelf import inventory as _shelf_inventory

# Erros de DOMÍNIO: a ferramenta rodou e recusou. Voltam como sucesso JSON-RPC
# com `ok: false` dentro do conteúdo, porque erro de protocolo aqui faria o
# servidor parecer quebrado quando quem errou foi a chamada.
_DOMINIO = (QueryError, DepositError, ExpandError, EntryNotFoundError,
            EntryAmbiguousError, FTS5MissingError, IndexSchemaError)

_CTX = {
    "project": {"type": "string",
                "description": "Project the caller is working in. Overrides "
                               "the workspace root reported by the client."},
    "session": {"type": "string",
                "description": "Caller session id, for session affinity."},
    "agent": {"type": "string", "description": "Agent depositing the grain."},
}


def _esquema(props: dict, obrigatorios: list) -> dict:
    """`additionalProperties: false` em TODO esquema. Sem isso um argumento
    desconhecido seria ignorado em silêncio, e é exatamente assim que um
    `restore: true` passaria despercebido no `neurata_expand`, fazendo uma
    superfície de leitura escrever."""
    return {"type": "object", "properties": props,
            "required": obrigatorios, "additionalProperties": False}


TOOLS = [
    {"name": "neurata_query",
     "description": "Search the archive deterministically (lexical + graph). "
                    "Supports facets like tag:, project:, class:, entity:.",
     "inputSchema": _esquema(
         {"q": {"type": "string", "description": "Query text and/or facets."},
          "limit": {"type": "integer", "description": "Max cards (default 10)."},
          "include_stale": {"type": "boolean",
                            "description": "Include tombstoned grains."},
          **_CTX},
         ["q"])},
    {"name": "neurata_deposit",
     "description": "Deposit raw content into the inbox. The next tick "
                    "catalogues it; nothing is ever destroyed.",
     "inputSchema": _esquema(
         {"content": {"type": "string", "description": "Raw content."},
          "title": {"type": "string", "description": "Optional title."},
          "type": {"type": "string", "description": "Grain type (default note)."},
          "env": {"type": "string", "description": "Environment tag."},
          **_CTX},
         ["content"])},
    # `restore` fica FORA de propósito: o roadmap manda superfície, não
    # capacidade, e restore escreve — é o único caminho de `expand` que
    # adquire o IndexLock.
    {"name": "neurata_expand",
     "description": "Open a grain further: card, summary or full body.",
     "inputSchema": _esquema(
         {"ref": {"type": "string", "description": "Grain id, slug or alias."},
          "grain": {"type": "string", "enum": ["card", "summary", "full"],
                    "description": "How much to open (default full)."}},
         ["ref"])},
    {"name": "neurata_shelf",
     "description": "Archive inventory, insights, or detected conflicts.",
     "inputSchema": _esquema(
         {"view": {"type": "string",
                   "enum": ["inventory", "insights", "conflicts"],
                   "description": "Which view (default inventory)."}},
         [])},
]

_POR_NOME = {t["name"]: t for t in TOOLS}


def _projeto_da_raiz(uri: str) -> "str | None":
    """Nome da última pasta da URI da raiz. `file:///c:/a/Repo` → `Repo`.

    Divido a string em vez de usar `Path`: a URI é sempre POSIX, inclusive no
    Windows, e `Path` local trataria `/c:/x` de forma dependente de plataforma.
    """
    try:
        caminho = unquote(urlparse(uri).path or "")
    except ValueError:
        return None
    return next((p for p in reversed(caminho.split("/")) if p), None)


class Servidor:
    def __init__(self, home_root: "str | Path | None" = None) -> None:
        self._home_root = home_root
        self._home: NeurataHome | None = None
        self.initialized = False
        # None = ainda não sei; [] = sei que não há raiz nenhuma.
        self.roots: list[str] | None = None
        self._saida: list = []
        self._proximo_id = 1
        self._id_roots: int | None = None

    # ---------------------------------------------------------------- home
    @property
    def home(self) -> NeurataHome:
        """Preguiçoso de propósito: abrir o índice custa 6 a 11 ms e o custo
        amortizado da primeira busca é invisível — medido. Pré-carregar na
        partida não compra nada e atrasa o handshake.

        O `init()` é obrigatório e não é detalhe: ele cria a árvore de pastas e
        o `config.json`. A `cli.main` o chama antes de qualquer despacho; sem
        ele, um `deposit` grava numa árvore que não existe. É idempotente.
        """
        if self._home is None:
            h = NeurataHome(self._home_root)
            h.init()
            self._home = h
        return self._home

    # ------------------------------------------------------------- contexto
    def contexto(self, args: dict) -> QueryContext:
        proj = (args.get("project") or "").strip() or None
        sess = (args.get("session") or "").strip() or None
        if proj:
            return QueryContext(project=proj, session=sess,
                                project_source="arg")
        if self.roots:
            nome = _projeto_da_raiz(self.roots[0])
            if nome:
                return QueryContext(project=nome, session=sess,
                                    project_source="roots")
        return QueryContext(project=None, session=sess, project_source="none")

    # ------------------------------------------------------ fila de pedidos
    def _pedir_roots(self) -> None:
        self._id_roots = self._proximo_id
        self._proximo_id += 1
        self._saida.append({"jsonrpc": "2.0", "id": self._id_roots,
                            "method": "roots/list", "params": {}})

    def pendentes(self) -> list:
        """Drena os pedidos que o servidor tem para o cliente. Quem chama é o
        laço principal, logo depois de tratar cada quadro."""
        saida, self._saida = self._saida, []
        return saida

    # -------------------------------------------------------------- despacho
    def handle(self, msg: Any) -> "dict | None":
        if not isinstance(msg, dict):
            return j.make_error(None, j.INVALID_REQUEST,
                                "request must be a JSON object")
        metodo = msg.get("method")
        if metodo is None:
            # É RESPOSTA do cliente a um pedido nosso. Só nos interessa a de
            # roots; id desconhecido é ignorado sem quebrar, porque um cliente
            # pode responder tarde a algo que já invalidamos.
            if msg.get("id") is not None and msg.get("id") == self._id_roots:
                self._absorver_roots(msg)
            return None
        ident = msg.get("id")
        try:
            resposta = self._rotear(metodo, msg.get("params") or {}, ident)
        except Exception:                                    # noqa: BLE001
            # O CLIENTE recebe uma mensagem genérica: traceback pode carregar
            # caminho de máquina. Mas esconder de TODO MUNDO deixa o operador
            # cego — foi assim que um `home.init()` faltando virou um "internal
            # error" mudo que custou uma rodada inteira de depuração. A spec do
            # transporte stdio diz que o stderr é livre; é exatamente para isto.
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            return (None if ident is None
                    else j.make_error(ident, j.INTERNAL_ERROR,
                                      "internal error"))
        return resposta

    def _absorver_roots(self, msg: dict) -> None:
        resultado = msg.get("result") or {}
        raizes = resultado.get("roots") or []
        self.roots = [r.get("uri", "") for r in raizes if isinstance(r, dict)]
        self._id_roots = None

    def _rotear(self, metodo: str, params: dict,
                ident: Any) -> "dict | None":
        if metodo == "initialize":
            return j.make_result(ident, self._initialize(params))
        if metodo == "notifications/initialized":
            self.initialized = True
            return None
        if metodo == "notifications/roots/list_changed":
            self.roots = None
            self._pedir_roots()
            return None
        if metodo == "ping":
            return None if ident is None else j.make_result(ident, {})
        if metodo == "tools/list":
            return None if ident is None else j.make_result(
                ident, {"tools": TOOLS})
        if metodo == "tools/call":
            return None if ident is None else self._chamar(params, ident)
        # Notificação desconhecida se ignora; requisição desconhecida responde.
        return (None if ident is None
                else j.make_error(ident, j.METHOD_NOT_FOUND,
                                  f"unknown method: {metodo}"))

    def _initialize(self, params: dict) -> dict:
        self.initialized = True
        pedida = params.get("protocolVersion")
        versao = (pedida if pedida in j.SUPPORTED_PROTOCOLS
                  else j.SUPPORTED_PROTOCOLS[-1])
        cliente = (params.get("capabilities") or {})
        # Só pede raízes se o CLIENTE declarou a capacidade. `roots` é
        # capacidade do cliente, não do servidor: não a declaramos aqui.
        if isinstance(cliente.get("roots"), dict):
            self._pedir_roots()
        return {"protocolVersion": versao,
                "serverInfo": {"name": "neurata", "version": __version__},
                "capabilities": {"tools": {"listChanged": False}},
                "neurata": {"contract_version": CONTRACT_VERSION}}

    # ------------------------------------------------------------ ferramentas
    def _chamar(self, params: dict, ident: Any) -> dict:
        nome = params.get("name")
        if not isinstance(nome, str) or nome not in _POR_NOME:
            return j.make_error(ident, j.METHOD_NOT_FOUND,
                                f"unknown tool: {nome}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return j.make_error(ident, j.INVALID_PARAMS,
                                "arguments must be an object")
        erro = _validar(_POR_NOME[nome]["inputSchema"], args)
        if erro:
            return j.make_error(ident, j.INVALID_PARAMS, erro)
        try:
            carga = self._executar(nome, args)
        except LockHeldError as e:
            # Não é falha de protocolo: alguém está reindexando ou dando tick.
            # Quem decide quando tentar de novo é o chamador; retentar aqui
            # esconderia latência dentro de uma chamada de ferramenta.
            return _ok(ident, {"ok": False, "error": str(e),
                               "retryable": True})
        except _DOMINIO as e:
            return _ok(ident, {"ok": False, "error": str(e)})
        return _ok(ident, carga)

    def _executar(self, nome: str, args: dict) -> dict:
        if nome == "neurata_query":
            r = _query(self.home, args["q"],
                       limit=int(args.get("limit", 10)),
                       context=self.contexto(args),
                       include_stale=bool(args.get("include_stale", False)))
            return {"ok": True, **r}
        if nome == "neurata_deposit":
            ctx = self.contexto(args)
            r = _deposit(self.home, args["content"],
                         title=args.get("title"),
                         dtype=args.get("type", "note"),
                         denv=args.get("env", "generic"),
                         agent=args.get("agent"),
                         session=ctx.session)
            return {"ok": True, **r}
        if nome == "neurata_expand":
            r = _expand(self.home, args["ref"],
                        grain=args.get("grain", "full"))
            return {"ok": True, **r}
        vista = args.get("view", "inventory")
        fn = {"inventory": _shelf_inventory, "insights": _shelf_insights,
              "conflicts": _shelf_conflicts}[vista]
        return {"ok": True, **fn(self.home)}


def _ok(ident: Any, carga: dict) -> dict:
    return j.make_result(ident, {"content": [
        {"type": "text", "text": json.dumps(carga, ensure_ascii=False)}]})


def _validar(esquema: dict, args: dict) -> "str | None":
    """Validação suficiente para o contrato, não um validador de JSON Schema.
    Checa o que o protocolo cobra: obrigatório presente, nada além do
    declarado, e `enum` respeitado."""
    props = esquema["properties"]
    for chave in esquema["required"]:
        if chave not in args:
            return f"missing required argument: {chave}"
    for chave, valor in args.items():
        if chave not in props:
            return f"unknown argument: {chave}"
        permitidos = props[chave].get("enum")
        if permitidos and valor not in permitidos:
            return (f"invalid value for {chave}: expected one of "
                    + ", ".join(permitidos))
    return None


def main(argv: "list[str] | None" = None) -> int:
    """Ponto de entrada `neurata-mcp`.

    A primeira coisa que acontece é o sequestro do descritor 1: ver a nota 1 no
    topo do módulo. Só depois disso é seguro importar ou chamar qualquer coisa
    que possa imprimir.
    """
    protocolo = os.fdopen(os.dup(1), "w", encoding="utf-8",
                          errors="replace", newline="\n")
    os.dup2(2, 1)                 # o descritor 1 passa a ser o stderr
    sys.stdout = sys.stderr       # e o objeto Python também

    entrada = sys.stdin
    with contextlib.suppress(AttributeError, ValueError):
        entrada.reconfigure(  # type: ignore[attr-defined]
            encoding="utf-8", errors="replace")
    # UTF-8 explícito nos dois lados: não delegamos às variáveis de ambiente do
    # `.mcp.json`. Cliente que esquecer PYTHONUTF8 num Windows cp1252 mataria o
    # processo no primeiro acento, e essa família de bug já cobrou duas vezes
    # deste projeto.

    argv = sys.argv[1:] if argv is None else argv
    raiz = None
    if "--home" in argv:
        raiz = argv[argv.index("--home") + 1]
    servidor = Servidor(home_root=raiz)

    def escrever(obj: dict) -> None:
        protocolo.write(json.dumps(obj, ensure_ascii=False) + "\n")
        protocolo.flush()

    for quadro in j.read_frames(entrada):
        if quadro is j.OVERSIZE:
            escrever(j.make_error(None, j.INVALID_REQUEST, "frame too large"))
            continue
        try:
            msg = json.loads(quadro)
        except json.JSONDecodeError:
            # Segue lendo: servidor que morre no primeiro byte torto é inútil.
            escrever(j.make_error(None, j.PARSE_ERROR, "parse error"))
            continue
        resposta = servidor.handle(msg)
        if resposta is not None:
            escrever(resposta)
        for pedido in servidor.pendentes():
            escrever(pedido)
    return 0


if __name__ == "__main__":                         # pragma: no cover
    sys.exit(main())
