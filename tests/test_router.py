"""tests/test_router.py"""
from neurata.router import parse, variants


def test_facets_extracted_and_removed():
    p = parse("rrf type:note tag:RAG env:generic project:x")
    assert p.tokens == ["rrf"]
    assert p.facets == {"type": "note", "env": "generic", "project": "x"}
    assert p.tags == ["rag"]  # lowercased


def test_phrase_protects_facet():
    p = parse('"type:skill literal" resto')
    assert p.phrases == ["type:skill literal"]
    assert p.facets == {}
    assert p.tokens == ["resto"]


def test_unbalanced_quote_is_literal():
    p = parse('busca "aberta')
    assert p.phrases == []
    assert "aberta" in p.tokens


def test_skill_hint():
    assert parse("como faço deploy").skill_hint
    assert parse("How do I deploy").skill_hint
    assert not parse("deploy").skill_hint


def test_symbols_only_has_no_text():
    assert not parse("*** !!! ---").has_text


def test_variants_raw_first_and_column_sets():
    vs = variants(parse("compactação"))
    assert vs[0].name == "raw"
    assert vs[0].match.startswith("{title aliases tags body}: (")
    assert '"compactação"' in vs[0].match
    norm = next(v for v in vs if v.name == "norm")
    assert norm.match.startswith(
        "{title_norm aliases_norm tags_norm body_norm}: (")
    assert '"compactacao"' in norm.match


def test_variants_dedupe():
    names = [v.name for v in variants(parse("casa"))]
    assert "singular" not in names  # idêntica à norm ("casa" sem s final)
    assert "plural" in names and "prefix" in names


def test_prefix_variant_star():
    pref = next(v for v in variants(parse("configur"))
                if v.name == "prefix")
    assert '"configur"*' in pref.match


def test_operators_are_quoted_not_parsed():
    vs = variants(parse("AND OR NEAR"))
    assert '"AND"' in vs[0].match  # literal, nunca operador


def test_phrase_survives_normalization_as_phrase():
    vs = variants(parse('"Motor Híbrido"'))
    norm = next(v for v in vs if v.name == "norm")
    assert '"motor hibrido"' in norm.match
