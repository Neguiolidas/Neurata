"""neurata/compact.py — compact manual: corpo -> summary, full pro archive.

CLI utilitário, FORA da fachada pública de 4 verbos (deposit/query/shelf/
expand). Compact NUNCA perde: qualquer corpo que sai vai pro archive ANTES
de ser sobrescrito. Idempotente: corpo já igual ao summary que seria escrito
-> no-op com aviso (cobre re-compact de corpo editado pós-compact, que é
full novo e compacta de novo na próxima chamada).

Compact é o **Miner** manual — produção mecânica de grão. Recusa em dois
casos, ambos destrutivos e silenciosos se permitidos: grão `refined` (o Miner
não rebaixa o que o DeepMiner refinou) e grão espelhado (o próximo `tick`
reescreve o corpo a partir da fonte e a compactação some).
"""
import os
from datetime import datetime, timezone

from neurata import archive
from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.grains import make_summary
from neurata.home import NeurataHome
from neurata.indexdb import regime_of
from neurata.reindex import reindex


def compact(home: NeurataHome, ref: str) -> dict:
    entry = resolve(home, ref)
    eid = str(entry.meta.get("id", ""))
    rel = str(entry.path.relative_to(home.root))
    if str(entry.meta.get("grain_quality", "")) == "refined":
        return {"action": "refused", "id": eid, "path": rel,
                "reason": "grão refined — o Miner mecânico não rebaixa o que "
                          "o DeepMiner refinou (monotonicidade)"}
    if regime_of(entry.meta) == "mirror":
        return {"action": "refused", "id": eid, "path": rel,
                "reason": "grão espelhado — o próximo tick reescreveria o "
                          "corpo a partir da fonte e a compactação sumiria "
                          "sem aviso"}
    body = entry.body
    summary = make_summary(body)
    if summary.strip() == body.strip():
        return {"action": "noop", "id": eid, "path": rel,
                "reason": "corpo já é o summary — nada a compactar"}
    sha = archive.put(home, body.encode("utf-8"))
    meta = dict(entry.meta)
    meta["derived_from"] = sha
    meta["updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _atomic_write(entry.path, serialize(meta, summary))
    reindex(home)
    return {"action": "compacted", "id": eid, "path": rel, "archived": sha}


def _atomic_write(path, text: str) -> None:
    tmp = path.parent / f".tmp-{os.getpid()}-{path.name}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
