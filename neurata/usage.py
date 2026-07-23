"""neurata/usage.py — log de uso append-only + agregação.

logs/usage.jsonl: uma linha JSON por evento, O_APPEND (escrita < PIPE_BUF,
atômica na prática — sem lock, sem leitor concorrente vendo linha
truncada). Best-effort: falha de escrita NUNCA propaga (quem chama decide
o que fazer com o retorno bool). Leitura tolerante: linha corrompida é
pulada e contada — nunca explode. Observe-only: nada aqui ajusta pesos
automaticamente (v2.x).
"""
import json
import os
from datetime import datetime, timezone

from neurata.home import NeurataHome

_LOG_NAME = "usage"
_INVOCATION_LOG = "usage.log"
_INVOCATION_FIELDS = ("ts", "cmd", "duration_ms", "ok")


def log_invocation(home: NeurataHome, cmd: str, duration_ms: int,
                   ok: bool) -> bool:
    """Append de 1 invocação de CLI em NEURATA_HOME/usage.log.

    Distinto de log_event (logs/usage.jsonl, eventos da shelf): aqui é uma
    linha por chamada da CLI, pra o gate `doctor last-tick` medir uso real.
    Best-effort, O_APPEND (escrita < PIPE_BUF, atômica na prática). Retorna
    True/False; NUNCA propaga falha.
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "cmd": cmd,
        "duration_ms": duration_ms,
        "ok": ok,
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    path = home.root / _INVOCATION_LOG
    try:
        fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def read_invocations(home: NeurataHome) -> dict:
    """{"entries": [record, ...], "corrupt_lines": int}.

    Ordem preservada. Linha corrompida (JSON inválido ou faltando campos
    obrigatórios) é pulada e contada — leitura tolerante a torn-line, nunca
    explode.
    """
    path = home.root / _INVOCATION_LOG
    entries: list[dict] = []
    corrupt = 0
    if not path.exists():
        return {"entries": entries, "corrupt_lines": corrupt}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            if not all(k in rec for k in _INVOCATION_FIELDS):
                raise KeyError
        except (ValueError, KeyError, TypeError):
            corrupt += 1
            continue
        entries.append(rec)
    return {"entries": entries, "corrupt_lines": corrupt}


def log_event(home: NeurataHome, event: str, entry_id: str,
              query: "str | None" = None,
              rank: "int | None" = None) -> bool:
    """Append de 1 evento. Retorna True/False (best-effort, nunca lança)."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "event": event,
        "entry_id": entry_id,
        "query": query,
        "rank": rank,
    }
    line = json.dumps(record, ensure_ascii=False) + "\n"
    path = home.logs / f"{_LOG_NAME}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except OSError:
        return False


def read_usage(home: NeurataHome) -> dict:
    """{entry_id: {impressions, expands, last_used}} + contagem de corrompidas.

    Retorno: {"entries": {...}, "corrupt_lines": int}. Linha corrompida
    (JSON inválido ou faltando campos obrigatórios) é pulada e contada.
    """
    path = home.logs / f"{_LOG_NAME}.jsonl"
    agg: dict[str, dict] = {}
    corrupt = 0
    if not path.exists():
        return {"entries": agg, "corrupt_lines": corrupt}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            entry_id = rec["entry_id"]
            event = rec["event"]
            ts = rec["ts"]
        except (ValueError, KeyError, TypeError):
            corrupt += 1
            continue
        if event not in ("query", "expand"):
            corrupt += 1
            continue
        slot = agg.setdefault(
            entry_id, {"impressions": 0, "expands": 0, "last_used": None})
        if event == "query":
            slot["impressions"] += 1
        else:
            slot["expands"] += 1
        if slot["last_used"] is None or ts > slot["last_used"]:
            slot["last_used"] = ts
    return {"entries": agg, "corrupt_lines": corrupt}
