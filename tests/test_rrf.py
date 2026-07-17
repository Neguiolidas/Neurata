"""tests/test_rrf.py"""
from neurata.rrf import fuse


def test_fuse_weighted():
    scores = fuse([(1.0, [1, 2]), (0.5, [2, 3])], k=60)
    assert scores[1] == 1.0 / 61
    assert scores[2] == 1.0 / 62 + 0.5 / 61
    assert scores[3] == 0.5 / 62
    assert scores[2] > scores[1] > scores[3]


def test_fuse_empty():
    assert fuse([]) == {}
    assert fuse([(1.0, [])]) == {}
