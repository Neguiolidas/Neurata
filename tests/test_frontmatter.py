"""tests/test_frontmatter.py"""
import pytest

from neurata.frontmatter import FrontmatterError, parse, serialize


def test_roundtrip():
    meta = {
        "id": "01ABC",
        "title": "Motor: Híbrido",
        "tags": ["rag", "fts5"],
        "source": {"host": "vm1", "origin": "manual"},
    }
    text = serialize(meta, "corpo aqui\n")
    meta2, body2 = parse(text)
    assert meta2 == meta
    assert body2 == "corpo aqui\n"


def test_no_frontmatter():
    meta, body = parse("só corpo\n")
    assert meta == {}
    assert body == "só corpo\n"


def test_quoted_special_chars():
    meta = {"title": 'a: b "c" [d]'}
    meta2, _ = parse(serialize(meta, ""))
    assert meta2 == meta


def test_unterminated_raises():
    with pytest.raises(FrontmatterError):
        parse("---\nkey: v\nsem fim")


def test_empty_list():
    meta2, _ = parse(serialize({"tags": []}, ""))
    assert meta2 == {"tags": []}


def test_list_item_with_comma():
    meta = {"tags": ["a,b", "c"]}
    meta2, _ = parse(serialize(meta, ""))
    assert meta2 == meta


def test_list_item_with_quotes_and_comma():
    meta = {"l": ['a "q", b', "c"]}
    meta2, _ = parse(serialize(meta, ""))
    assert meta2 == meta


def test_serialize_deep_dict_raises():
    with pytest.raises(FrontmatterError):
        serialize({"a": {"b": {"c": "d"}}}, "")


def test_serialize_list_in_dict_raises():
    with pytest.raises(FrontmatterError):
        serialize({"a": {"b": ["x"]}}, "")
