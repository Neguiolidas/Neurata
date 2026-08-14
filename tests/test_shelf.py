"""tests/test_shelf.py"""
import math
from datetime import datetime, timedelta, timezone

from neurata import shelf


def _cfg(**over):
    base = {"w_u": 1.0, "w_r": 1.0, "w_c": 0.5, "tau_dias": 30.0}
    base.update(over)
    return base


def test_compute_score_formula():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    updated = (now - timedelta(days=30)).isoformat()
    score = shelf.compute_score(_cfg(), impressions=10, expands=2,
                                updated=updated, grain_quality="mechanical",
                                now=now)
    expected = (1.0 * math.log1p(2 + 0.1 * 10)
                + 1.0 * math.exp(-30 / 30.0)
                + 0.5 * 0.0)
    assert math.isclose(score, expected, rel_tol=1e-9)


def test_compute_score_refined_adds_curadoria():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    updated = now.isoformat()
    mech = shelf.compute_score(_cfg(), 0, 0, updated, "mechanical", now=now)
    ref = shelf.compute_score(_cfg(), 0, 0, updated, "refined", now=now)
    assert math.isclose(ref - mech, 0.5, rel_tol=1e-9)


def test_compute_score_no_updated_no_recencia_penalty_no_crash():
    now = datetime(2026, 7, 18, tzinfo=timezone.utc)
    score = shelf.compute_score(_cfg(), 0, 0, "", "mechanical", now=now)
    assert score >= 0  # delta_dias=0 -> recencia máxima (não penaliza ausência)


def test_normalize_minmax():
    assert shelf.normalize([1.0, 2.0, 3.0]) == [0.0, 0.5, 1.0]


def test_normalize_all_equal_sem_divisao_por_zero():
    assert shelf.normalize([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]


def test_normalize_vazio():
    assert shelf.normalize([]) == []


def test_apply_boost_multiplica_final():
    cards = [
        {"score": 1.0, "shelf_score": 0.0},
        {"score": 1.0, "shelf_score": 10.0},
    ]
    shelf.apply_boost(cards, beta=0.15)
    assert cards[0]["score"] == 1.0 * (1 + 0.15 * 0.0)
    assert cards[1]["score"] == 1.0 * (1 + 0.15 * 1.0)
    assert cards[1]["score"] > cards[0]["score"]


def test_apply_boost_beta_zero_desliga():
    cards = [
        {"score": 1.0, "shelf_score": 0.0},
        {"score": 2.0, "shelf_score": 99.0},
    ]
    before = [c["score"] for c in cards]
    shelf.apply_boost(cards, beta=0.0)
    assert [c["score"] for c in cards] == before


def test_apply_boost_lista_vazia_nao_lanca():
    shelf.apply_boost([], beta=0.15)


def test_conflicts_ignores_self_reference(tmp_path):
    """Grão nunca conflita consigo mesmo (dado gravado por versões < 1.3)."""
    from neurata.home import NeurataHome

    home = NeurataHome(tmp_path)
    home.init()
    (home.library / "solo.md").write_text(
        "---\nid: 01SELF0000000000000000A\ntitle: Solo\n"
        "conflicts_with: [01SELF0000000000000000A]\n---\ncorpo\n",
        encoding="utf-8")
    (home.library / "par.md").write_text(
        "---\nid: 01PAR00000000000000000B\ntitle: Par\n"
        "conflicts_with: [01SELF0000000000000000A]\n---\noutro corpo\n",
        encoding="utf-8")

    result = shelf.conflicts(home)

    paths = [c["path"] for c in result["conflicts_with"]]
    assert paths == ["library/par.md"]
