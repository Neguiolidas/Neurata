"""neurata/compact.py — compact manual: corpo -> summary, full pro archive.

CLI utilitário, FORA da fachada pública de 4 verbos (deposit/query/shelf/
expand). Compact NUNCA perde: qualquer corpo que sai vai pro archive ANTES
de ser sobrescrito (ordem crash-safe: archive -> arquivo -> índice). Idempo-
tente: corpo já igual ao summary que seria escrito -> no-op com aviso (cobre
re-compact de corpo editado pós-compact, que é full novo e compacta de novo
na próxima chamada).

Compact é o **Miner** manual — produção mecânica de grão. Recusa só grão
`refined` (o Miner mecânico não rebaixa o que o DeepMiner refinou;
monotonicidade). Grão espelhado (`regime='mirror'`) é aceito desde a v1.4:
o tick sabe conviver com espelho compactado (spec v1.4 §1-2).
`reindex_after=False` pula o `reindex` completo no fim — usado pelo chamador
em lote do tick (fase v1.4 seguinte), que atualiza o índice grão a grão em
vez de reconstruir tudo a cada compactação. O CLI manual mantém o default
`True` e o comportamento de hoje.
"""
import os
from datetime import datetime, timezone

from neurata import archive
from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.grains import make_summary
from neurata.home import NeurataHome
from neurata.reindex import reindex


def compact(home: NeurataHome, ref: str, reindex_after: bool = True) -> dict:
    entry = resolve(home, ref)
    eid = str(entry.meta.get("id", ""))
    rel = str(entry.path.relative_to(home.root))
    if str(entry.meta.get("grain_quality", "")) == "refined":
        return {"action": "refused", "id": eid, "path": rel,
                "reason": "grão refined — o Miner mecânico não rebaixa o que "
                          "o DeepMiner refinou (monotonicidade)"}
    body = entry.body
    summary = make_summary(body)
    if summary.strip() == body.strip():
        return {"action": "noop", "id": eid, "path": rel,
                "reason": "corpo já é o summary — nada a compactar"}
    sha = archive.put(home, body.encode("utf-8"))  # 1º: full salvo antes
    meta = dict(entry.meta)
    meta["derived_from"] = sha
    meta["updated"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds")
    _atomic_write(entry.path, serialize(meta, summary))  # 2º: arquivo
    if reindex_after:
        reindex(home)  # 3º: índice
    return {"action": "compacted", "id": eid, "path": rel, "archived": sha}


def _atomic_write(path, text: str) -> None:
    tmp = path.parent / f".tmp-{os.getpid()}-{path.name}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
