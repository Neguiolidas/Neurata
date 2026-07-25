"""neurata/shelf.py — shelf score (uso + recência + curadoria) + inventário.

score = w_u*log1p(uso_pond) + w_r*exp(-Δdias/tau) + w_c*(1 se refined)
uso_pond = expands + 0.1*impressions

Estágio final do ranking (abordagem A): NÃO entra na fusão RRF (score
estático independe da query). Aplica-se só ao top-K já decidido:
final = rrf * (1 + beta * shelf_norm), shelf_norm = min-max dentro do
top-K. beta=0 desliga limpo. Scores todos iguais -> norm=0 (sem /0).
"""
import json
import math
from datetime import datetime, timezone

from neurata import config as query_config
from neurata import usage
from neurata.frontmatter import FrontmatterError
from neurata.frontmatter import parse as parse_fm
from neurata.home import NeurataHome
from neurata.indexdb import connect


def _parse_iso(s: str) -> "datetime | None":
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_score(cfg_shelf: dict, impressions: int, expands: int,
                   updated: str, grain_quality: str,
                   now: "datetime | None" = None) -> float:
    now = now or datetime.now(timezone.utc)
    uso_pond = expands + 0.1 * impressions
    w_u = cfg_shelf["w_u"]
    w_r = cfg_shelf["w_r"]
    w_c = cfg_shelf["w_c"]
    tau = cfg_shelf["tau_dias"]
    dt = _parse_iso(updated)
    delta_dias = 0.0 if dt is None else max(
        0.0, (now - dt).total_seconds() / 86400.0)
    recencia = math.exp(-delta_dias / tau) if tau > 0 else 0.0
    curadoria = 1.0 if grain_quality == "refined" else 0.0
    return (w_u * math.log1p(uso_pond) + w_r * recencia + w_c * curadoria)


def normalize(scores: list[float]) -> list[float]:
    """Min-max dentro do conjunto. Todos iguais -> 0 pra todos (sem /0)."""
    if not scores:
        return []
    lo, hi = min(scores), max(scores)
    span = hi - lo
    if span == 0:
        return [0.0] * len(scores)
    return [(s - lo) / span for s in scores]


def apply_boost(cards: list[dict], beta: float) -> None:
    """Aplica final = rrf*(1+beta*shelf_norm) in-place em cards['score'].

    Espera cards já ordenados/truncados ao top-K, cada um com
    'score' (rrf/ppr fundido) e 'shelf_score' (bruto, de compute_score).
    """
    if not cards:
        return
    norm = normalize([c["shelf_score"] for c in cards])
    for card, n in zip(cards, norm, strict=True):
        card["score"] = card["score"] * (1 + beta * n)


def inventory(home: NeurataHome) -> dict:
    """Inventário completo, ordenado por score desc.

    Nunca-consultado (zero impressions e expands) NÃO some da lista —
    aparece marcado `candidato_arquivamento` (anti viés-de-disponibilidade:
    o item precisa aparecer pra alguém decidir arquivar, não sumir sozinho).
    """
    cfg_shelf = query_config.load(home)["shelf"]
    con = connect(home)
    try:
        rows = con.execute(
            "SELECT id, slug, title, type, updated, grain_quality"
            " FROM entries").fetchall()
    finally:
        con.close()
    agg = usage.read_usage(home)["entries"]
    items = []
    for eid, slug, title, etype, updated, gq in rows:
        u = agg.get(eid, {"impressions": 0, "expands": 0, "last_used": None})
        score = compute_score(cfg_shelf, u["impressions"], u["expands"],
                              updated, gq)
        items.append({
            "id": eid, "slug": slug, "title": title, "type": etype,
            "score": round(score, 6),
            "impressions": u["impressions"], "expands": u["expands"],
            "last_used": u.get("last_used"),
            "candidato_arquivamento": (u["impressions"] == 0
                                      and u["expands"] == 0),
        })
    items.sort(key=lambda c: (-c["score"], c["slug"]))
    return {"items": items}


def insights(home: NeurataHome) -> dict:
    """Observe-only: top consultados/expands, nunca-consultados, conflitos.

    Zero side-effects — só lê usage.jsonl + índice, nunca ajusta pesos
    (auto-tuning é v2.x, fora de escopo aqui).
    """
    items = inventory(home)["items"]
    conf = conflicts(home)
    return {
        "top_consultados": sorted(
            [c for c in items if c["impressions"] > 0],
            key=lambda c: (-c["impressions"], c["slug"]))[:10],
        "nunca_consultados": [c for c in items
                              if c["candidato_arquivamento"]],
        "top_expands": sorted(
            [c for c in items if c["expands"] > 0],
            key=lambda c: (-c["expands"], c["slug"]))[:10],
        "total_conflitos": (len(conf["conflicts_with"])
                           + len(conf["collisions"])),
    }


def conflicts(home: NeurataHome) -> dict:
    """`conflicts_with` do frontmatter + colisões id/slug do último reindex.

    Near-dup automático é v0.4 (fora de escopo) — aqui só EXPÕE o que já
    está marcado (manual) ou já foi detectado barato no reindex.
    """
    manual = []
    for base in (home.library, home.inbox):
        for path in sorted(base.rglob("*.md")):
            try:
                meta, _ = parse_fm(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, FrontmatterError):
                continue
            cw = meta.get("conflicts_with", [])
            if isinstance(cw, str):
                cw = [cw] if cw.strip() else []
            if cw:
                manual.append({
                    "id": str(meta.get("id", "")),
                    "path": str(path.relative_to(home.root)),
                    "conflicts_with": cw,
                })
    collisions = []
    raw = _meta_value(home, "skipped")
    for s in (json.loads(raw) if raw else []):
        if s.get("reason") in ("id-collision", "slug-collision"):
            collisions.append(s)
    return {"conflicts_with": manual, "collisions": collisions}


def _meta_value(home: NeurataHome, key: str) -> "str | None":
    if not home.index_path.exists():
        return None
    import sqlite3
    con = sqlite3.connect(home.index_path)
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?",
                          (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        con.close()
