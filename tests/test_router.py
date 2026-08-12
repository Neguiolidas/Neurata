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


def test_morfologia_mora_dentro_da_variante_norm():
    """Flexão é expansão de termo, não lista própria: sem lista degenerada."""
    vs = variants(parse("casa"))
    assert [v.name for v in vs] == ["raw", "norm", "prefix"]
    norm = next(v for v in vs if v.name == "norm")
    assert '"casa"' in norm.match and '"casas"' in norm.match


def test_token_normalizado_multipalavra_vira_frase_unica():
    """`0.10` não pode virar `0` OR `10` — casaria qualquer `3.0`."""
    norm = next(v for v in variants(parse("0.10")) if v.name == "norm")
    assert '"0 10"' in norm.match
    assert '"0" OR "10"' not in norm.match


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


def test_facet_regime_e_extraida():
    parsed = parse("regime:curated token")
    assert parsed.facets["regime"] == "curated"
    assert parsed.tokens == ["token"]  # faceta sai do texto de busca


# ---- v1.1: facetas de procedência + missing: ----------------------------

def test_provenance_facets_extracted():
    p = parse("rrf agent:hermes session:s-1 origin:manual")
    assert p.tokens == ["rrf"]
    assert p.facets == {
        "agent": "hermes", "session": "s-1", "origin": "manual"}


def test_missing_is_conjunction():
    p = parse("missing:agent missing:origin")
    assert p.missing == ["agent", "origin"]
    assert p.tokens == []
    assert p.has_facets


def test_missing_unknown_key_is_parsed_not_judged():
    """`parse` não valida: quem lista as chaves válidas é `query`."""
    assert parse("missing:xpto").missing == ["xpto"]


def test_quoted_provenance_facet_is_literal():
    p = parse('"agent:hermes" resto')
    assert p.phrases == ["agent:hermes"]
    assert p.facets == {}
    assert p.tokens == ["resto"]


def test_missing_repeated_dedups_preserving_order():
    assert parse("missing:origin missing:agent missing:origin").missing == [
        "origin", "agent"]


def test_grammar_covers_every_provenance_column():
    """A gramática do parser e o schema do índice não podem divergir em
    silêncio: coluna nova sem facet vira token de texto sem ninguém notar."""
    from neurata.indexdb import PROVENANCE_COLS
    q = " ".join(f"{col}:v{i}" for i, col in enumerate(PROVENANCE_COLS))
    p = parse(q)
    assert set(p.facets) == set(PROVENANCE_COLS)
    assert p.tokens == []
    assert parse(" ".join(f"missing:{c}" for c in PROVENANCE_COLS)).missing \
        == list(PROVENANCE_COLS)
