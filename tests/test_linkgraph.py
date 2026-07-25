"""tests/test_linkgraph.py"""
from neurata.linkgraph import neighbors, ppr

_ADJ = {1: {2}, 2: {1, 3}, 3: {2}, 4: {5}, 5: {4}}


def test_neighbors_one_hop():
    assert neighbors(_ADJ, [1]) == {2}
    assert neighbors(_ADJ, [1, 4]) == {2, 5}
    assert neighbors(_ADJ, []) == set()


def test_ppr_deterministic_and_conserves_mass():
    p1 = ppr(_ADJ, [1])
    p2 = ppr(_ADJ, [1])
    assert p1 == p2
    assert abs(sum(p1.values()) - 1.0) < 1e-9


def test_ppr_proximity_ordering():
    p = ppr(_ADJ, [1])
    assert p[2] > p[3]          # 1 hop > 2 hops
    assert p.get(4, 0.0) == 0.0  # componente desconexo não recebe massa


def test_ppr_empty_cases():
    assert ppr({}, [1]) == {}
    assert ppr(_ADJ, []) == {}
