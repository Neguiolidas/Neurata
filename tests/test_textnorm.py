"""tests/test_textnorm.py"""
from neurata.textnorm import normalize, slugify, split_identifiers, strip_accents


def test_strip_accents_pt():
    assert strip_accents("compactação é ótima") == "compactacao e otima"


def test_split_identifiers():
    assert split_identifiers("curate_tick e NeurataHome") == "curate tick e Neurata Home"


def test_normalize_pipeline():
    assert normalize("Compactação do curate_tick!") == "compactacao do curate tick"


def test_normalize_camel_boundary_with_accents():
    assert normalize("SeçãoÍndice") == "secao indice"
    assert normalize("caféTest") == "cafe test"


def test_slugify():
    assert slugify("Decisão: Motor Híbrido (v1)") == "decisao-motor-hibrido-v1"
    assert slugify("///") == "untitled"
    assert len(slugify("x" * 200)) <= 60
