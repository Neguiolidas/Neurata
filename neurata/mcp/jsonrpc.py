"""neurata/mcp/jsonrpc.py — JSON-RPC 2.0 sobre stdio, só biblioteca padrão.

Enquadramento por LINHA: uma mensagem é exatamente uma linha de JSON terminada
em `\\n`. Não há cabeçalho `Content-Length` — a especificação do transporte
stdio do MCP usa ndjson, e o servidor do Conscio, medido, faz o mesmo.

O teto por quadro existe para um cliente doente não fazer o servidor alocar a
linha inteira na memória: acima dele o excesso é DRENADO até a próxima quebra e
devolvemos o sentinela `OVERSIZE`, de modo que o quadro seguinte continue sendo
lido. Descartar o excesso não pode descartar a sessão.
"""
from collections.abc import Iterator
from typing import Any

# Os cinco do JSON-RPC 2.0. Erro de PROTOCOLO usa estes; erro de DOMÍNIO não —
# ver `neurata/mcp/server.py`, que devolve sucesso com `ok: false` dentro.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Da mais velha para a mais nova; a última é a queda quando o cliente pede uma
# versão que não conhecemos.
SUPPORTED_PROTOCOLS = ["2024-11-05", "2025-03-26", "2025-06-18"]

DEFAULT_MAX_FRAME_BYTES = 1_048_576  # 1 MiB


class _Oversize:
    """Sentinela de quadro grande demais. Classe própria em vez de `object()`
    para o `repr` dizer o que é quando aparecer num traceback."""

    def __repr__(self) -> str:  # pragma: no cover - conveniência de depuração
        return "<quadro acima do teto>"


OVERSIZE = _Oversize()


def make_error(ident: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident,
            "error": {"code": code, "message": message}}


def make_result(ident: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def _drain_to_newline(stream, chunk: int) -> None:
    while True:
        piece = stream.readline(chunk)
        if piece == "" or piece.endswith("\n"):
            return


def read_frames(stream,
                max_bytes: int = DEFAULT_MAX_FRAME_BYTES) -> Iterator[Any]:
    """Gera um quadro por linha não vazia; `OVERSIZE` quando passa do teto.

    Linha em branco é ignorada de propósito: um `\\n` extra do cliente não é
    erro de sintaxe, e responder PARSE_ERROR a ele poluiria o canal com um erro
    que não existe.
    """
    while True:
        line = stream.readline(max_bytes + 1) if max_bytes else stream.readline()
        if line == "":
            return
        if max_bytes and len(line) > max_bytes and not line.endswith("\n"):
            _drain_to_newline(stream, max_bytes + 1)
            yield OVERSIZE
            continue
        stripped = line.rstrip("\n")
        if stripped.strip():
            yield stripped
