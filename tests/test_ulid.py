"""tests/test_ulid.py"""
import re

from neurata.ulid import new_ulid


def test_shape_and_charset():
    u = new_ulid()
    assert len(u) == 26
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", u)


def test_time_ordering():
    a = new_ulid(ts_ms=1_000_000)
    b = new_ulid(ts_ms=2_000_000)
    assert a < b


def test_uniqueness_same_ms():
    us = {new_ulid(ts_ms=5) for _ in range(200)}
    assert len(us) == 200
