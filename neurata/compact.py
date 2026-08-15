"""neurata/compact.py — compact manual: corpo -> summary, full pro archive.

CLI utilitário, FORA da fachada pública de 4 verbos (deposit/query/shelf/
expand). Compact NUNCA perde: qualquer corpo que sai vai pro archive ANTES
de ser sobrescrito (ordem crash-safe: archive -> arquivo -> índice). Idempo-
tente e monotônico no tamanho: só escreve quando o summary ENCOLHE o corpo
(ponto fixo e corpos que o summary infla — heading atrás de heading — caem
no mesmo no-op com aviso). Cobre re-compact de corpo editado pós-compact,
que é full novo e compacta de novo na próxima chamada.

Compact é o **Miner** manual — produção mecânica de grão. Recusa em dois
casos, ambos trabalho que evapora sem aviso se permitidos: grão `refined`
(o Miner mecânico não rebaixa o que o DeepMiner refinou; monotonicidade) e
grão espelhado (`regime='mirror'`), cujo corpo o próximo `tick` reescreve a
partir da fonte — `_sync_update_in_place` grava o corpo novo e derruba o
`derived_from` junto, deixando o blob órfão no archive e o grão do tamanho
original. Compactar espelho não corrompe nada, mas é trabalho desfeito na
volta seguinte: recusar na hora é a resposta honesta.
"""
import os

from neurata import archive
from neurata.entryref import resolve
from neurata.frontmatter import serialize
from neurata.grains import make_summary
from neurata.home import NeurataHome
from neurata.indexdb import LockHeldError, regime_of
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
    # Ponto fixo (summary == corpo) e não-ganho (summary >= corpo) são o
    # mesmo caso para quem chama: não há o que compactar. Sem a segunda
    # metade, um corpo só de headings consecutivos ("# A\n# B") volta MAIOR
    # do que entrou — `make_summary` junta blocos com "\n\n" e o original
    # tinha "\n" — e o corpo servido crescia em nome de "compactar".
    if len(summary.strip()) >= len(body.strip()):
        return {"action": "noop", "id": eid, "path": rel,
                "reason": "summary não encolhe o corpo — nada a compactar"}
    sha = archive.put(home, body.encode("utf-8"))  # 1º: full salvo antes
    meta = dict(entry.meta)
    meta["derived_from"] = sha
    # `updated` NÃO é carimbado: compactar troca a representação do grão,
    # não o que ele diz — o original volta byte-a-byte do archive. A data
    # responde "quando este conteúdo mudou para quem lê", e a manutenção
    # não muda isso. Carimbar aqui zerava a idade de todo o acervo de uma
    # vez, e como o shelf pontua recência (`exp(-Δdias/tau)`), os grãos
    # recém-compactados subiam no ranking e expulsavam do topo material
    # intacto — inclusive curado. Quem precisa saber que a compactação
    # ocorreu lê `derived_from`/`derived_hash`; a data não mente por isso.
    _atomic_write(entry.path, serialize(meta, summary))  # 2º: arquivo
    result = {"action": "compacted", "id": eid, "path": rel, "archived": sha}
    # Archive e arquivo já estão escritos: a compactação ACONTECEU. Deixar o
    # `LockHeldError` subir daqui reportaria falha (rc=2) de um trabalho
    # feito, e o operador tentaria de novo à toa. O índice é o único
    # atrasado, e ele se acerta sozinho no próximo tick ou `reindex` — o que
    # o retorno diz, em vez de mentir "falhou".
    try:
        reindex(home)  # 3º: índice
    except LockHeldError:
        result["action"] = "compacted-pending-index"
        result["reason"] = ("índice travado por outro processo — a "
                            "compactação está em disco; rode `neurata "
                            "reindex` (ou espere o próximo tick)")
    return result


def _atomic_write(path, text: str) -> None:
    tmp = path.parent / f".tmp-{os.getpid()}-{path.name}"
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
