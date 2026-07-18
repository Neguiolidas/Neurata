"""neurata/shelf.py — shelf score (uso + recência + curadoria) + inventário.

score = w_u*log1p(uso_pond) + w_r*exp(-Δdias/tau) + w_c*(1 se refined)
uso_pond = expands + 0.1*impressions

Estágio final do ranking (abordagem A): NÃO entra na fusão RRF (score
estático independe da query). Aplica-se só ao top-K já decidido:
final = rrf * (1 + beta * shelf_norm), shelf_norm = min-max dentro do
top-K. beta=0 desliga limpo. Scores todos iguais -> norm=0 (sem /0).
"""
import math
from datetime import datetime, timezone


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
    if dt is None:
        delta_dias = 0.0
    else:
        delta_dias = max(0.0, (now - dt).total_seconds() / 86400.0)
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
    for card, n in zip(cards, norm):
        card["score"] = card["score"] * (1 + beta * n)
