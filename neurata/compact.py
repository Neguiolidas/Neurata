"""neurata/compact.py — compact manual: corpo -> summary, full pro archive.

CLI utilitário, FORA da fachada pública de 4 verbos (deposit/query/shelf/
expand). Compact NUNCA recusa nem perde: qualquer corpo que sai vai pro
archive ANTES de ser sobrescrito. Idempotente: corpo já igual ao summary
que seria escrito -> no-op com aviso (cobre re-compact de corpo editado
pós-compact, que é full novo e compacta de novo na próxima chamada).
"""
import os
from datetime import datetime, timezone

from neurata import archive
from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.grains import make_summary
from neurata.home import NeurataHome
from neurata.reindex import reindex


def compact(home: NeurataHome, ref: str) -> dict:
    entry = resolve(home, ref)
    eid = str(entry.meta.get("id", ""))
    body = entry.body
    summary = make_summary(body)
    if summary.strip() == body.strip():
        return {"action": "noop", "id": eid,
                "path": str(entry.path.relative_to(home.root)),
                "reason": "corpo já é o summary — nada a compactar"}
    sha = archive.put(home, body.encode("utf-8"))
    meta = dict(entry.meta)
    meta["derived_from"] = sha
    meta["updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _atomic_write(entry.path, serialize(meta, summary))
    reindex(home)
    return {"action": "compacted", "id": eid,
            "path": str(entry.path.relative_to(home.root)), "archived": sha}


def _atomic_write(path, text: str) -> None:
    tmp = path.parent / f".tmp-{os.getpid()}-{path.name}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
