"""neurata/expand.py — fachada pública: grão maior sob demanda + --restore.

Sem flag imprime o full (CLI stateless: quem chama `expand` quer o full).
`--grain card|summary|full` pede grão específico. Roundtrip byte-a-byte:
compact -> expand --restore devolve o corpo original exatamente. Toda a
leitura de grão é função pura de (meta, body) — expand funciona MESMO com
o índice destruído (só depende de .md + archive/).
"""
import os
from datetime import datetime, timezone

from neurata import archive, usage
from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.grains import make_card, make_summary
from neurata.home import NeurataHome
from neurata.reindex import reindex

_GRAINS = ("card", "summary", "full")


class ExpandError(ValueError):
    pass


def expand(home: NeurataHome, ref: str, grain: str = "full",
           restore: bool = False) -> dict:
    entry = resolve(home, ref)
    eid = str(entry.meta.get("id", ""))
    if restore:
        result = _restore(home, entry)
    else:
        if grain not in _GRAINS:
            raise ExpandError(
                f"grain inválido: {grain!r} (esperado card|summary|full)")
        result = {"id": eid, "grain": grain, "text": _text(home, entry, grain)}
    usage.log_event(home, "expand", eid)
    return result


def _text(home: NeurataHome, entry, grain: str) -> str:
    if grain == "card":
        return make_card(entry.meta, entry.body)
    if grain == "summary":
        # Regra anti summary-de-summary: entrada já compactada tem o
        # summary NO PRÓPRIO corpo — não re-derivar (mesma regra do
        # reindex, replicada aqui pra não depender do índice).
        if entry.meta.get("derived_from"):
            return entry.body
        return make_summary(entry.body)
    return _full_text(home, entry)  # grain == "full"


def _full_text(home: NeurataHome, entry) -> str:
    derived_from = entry.meta.get("derived_from")
    if not derived_from:
        return entry.body
    return archive.get(home, str(derived_from)).decode("utf-8")


def _restore(home: NeurataHome, entry) -> dict:
    derived_from = entry.meta.get("derived_from")
    eid = str(entry.meta.get("id", ""))
    if not derived_from:
        return {"id": eid, "action": "noop",
                "reason": "entrada não está compactada (sem derived_from)"}
    full = archive.get(home, str(derived_from)).decode("utf-8")
    # (a) arquiva o corpo ATUAL primeiro — protege edição manual pós-compact
    # de sobrescrita cega (o summary corrente também vira recuperável).
    archive.put(home, entry.body.encode("utf-8"))
    meta = dict(entry.meta)
    del meta["derived_from"]
    meta["updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _atomic_write(entry.path, serialize(meta, full))
    reindex(home)
    return {"id": eid, "action": "restored",
            "path": str(entry.path.relative_to(home.root))}


def _atomic_write(path, text: str) -> None:
    tmp = path.parent / f".tmp-{os.getpid()}-{path.name}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
