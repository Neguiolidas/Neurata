"""tests/test_query.py — end-to-end do pipeline."""
import pytest

from neurata.home import NeurataHome
from neurata.query import QueryError, query
from neurata.reindex import reindex


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def _write(home, name, meta_lines, body):
    (home.library / f"{name}.md").write_text(
        "---\n" + "\n".join(meta_lines) + "\n---\n" + body)


def _setup(tmp_path):
    home = _home(tmp_path)
    _write(home, "motor-hibrido",
           ["id: 01A", "title: Motor Híbrido", "tags: [rag, busca]",
            "aliases: [hybrid-motor]"],
           "BM25 e vetores fundidos por RRF. Liga com [[compactacao]].\n")
    _write(home, "compactacao",
           ["id: 01B", "title: Compactação", "type: decision"],
           "Compactação recuperável via archive.\n")
    _write(home, "deploy-skill",
           ["id: 01C", "title: Deploy Skill", "type: skill"],
           "Passos de deploy do serviço.\n")
    _write(home, "deploy-note",
           ["id: 01D", "title: Deploy Note", "type: note"],
           "Passos de deploy do serviço.\n")
    reindex(home)
    return home


def test_lexical_hit_with_snippet(tmp_path):
    res = query(_setup(tmp_path), "vetores fundidos")["results"]
    assert res and res[0]["slug"] == "motor-hibrido"
    assert res[0]["via"] == "lexical"
    assert res[0]["snippet"]
    assert res[0]["score"] > 0


def test_accent_insensitive(tmp_path):
    res = query(_setup(tmp_path), "compactacao")["results"]
    assert any(c["slug"] == "compactacao" for c in res)


def test_alias_match(tmp_path):
    res = query(_setup(tmp_path), "hybrid")["results"]
    assert any(c["slug"] == "motor-hibrido" for c in res)


def test_graph_neighbor_surfaces(tmp_path):
    res = query(_setup(tmp_path), "vetores RRF")["results"]
    by_slug = {c["slug"]: c for c in res}
    # compactacao não contém os termos — entra pelo link do seed
    assert by_slug["compactacao"]["via"] == "graph"
    assert by_slug["compactacao"]["snippet"] is None
    assert by_slug["motor-hibrido"]["score"] > by_slug["compactacao"]["score"]


def test_facet_filter_mixed(tmp_path):
    res = query(_setup(tmp_path), "deploy type:skill")["results"]
    assert res and all(c["type"] == "skill" for c in res)


def test_facet_only_listing(tmp_path):
    res = query(_setup(tmp_path), "type:note")["results"]
    assert res
    assert all(c["via"] == "facet" and c["score"] is None
               and c["snippet"] is None for c in res)


def test_tag_filter(tmp_path):
    res = query(_setup(tmp_path), "tag:RAG")["results"]
    assert [c["slug"] for c in res] == ["motor-hibrido"]


def test_skill_boost(tmp_path):
    res = query(_setup(tmp_path), "como faço deploy")["results"]
    skill = next(c for c in res if c["slug"] == "deploy-skill")
    note = next(c for c in res if c["slug"] == "deploy-note")
    assert skill["score"] > note["score"]


def test_limit(tmp_path):
    home = _setup(tmp_path)
    assert len(query(home, "deploy", limit=1)["results"]) == 1


def test_empty_query_error(tmp_path):
    with pytest.raises(QueryError, match="vazia"):
        query(_setup(tmp_path), "   ")


def test_symbols_only_error(tmp_path):
    with pytest.raises(QueryError, match="vazia"):
        query(_setup(tmp_path), "*** !!!")


def test_schema_mismatch_remediation(tmp_path):
    home = _home(tmp_path)  # nunca reindexado
    with pytest.raises(QueryError, match="reindex"):
        query(home, "x")


def test_acronym_split_findable_by_parts(tmp_path):
    """T4: termo com acrônimo/camelCase (ex. JSONParserUtil) é indexado
    com dupla tokenização (token completo + partes split), igual ao
    padrão PT já existente — busca por uma parte isolada (`JSON`,
    `Parser`) precisa achar a entrada mesmo sem o termo completo."""
    home = _home(tmp_path)
    _write(home, "parser-util",
           ["id: 01E", "title: JSONParserUtil", "type: note"],
           "Utilitário interno, sem relação com os outros termos.\n")
    reindex(home)
    res_json = query(home, "JSON")["results"]
    assert any(c["slug"] == "parser-util" for c in res_json)
    res_parser = query(home, "Parser")["results"]
    assert any(c["slug"] == "parser-util" for c in res_parser)
    res_util = query(home, "Util")["results"]
    assert any(c["slug"] == "parser-util" for c in res_util)
    # o termo completo original (sem split) também precisa achar — a
    # coluna raw preserva "JSONParserUtil" intacto.
    res_full = query(home, "JSONParserUtil")["results"]
    assert any(c["slug"] == "parser-util" for c in res_full)


def test_malicious_queries_never_leak_fts_errors(tmp_path):
    home = _setup(tmp_path)
    malignas = ['a AND b', 'x "quebra', 'a(b)c', 'NEAR(a b)', 'col:val',
                '{title}: x', 'a*b', 'a"b"c', '"“aspas tortas”"',
                'a NOT b OR c', '-x --y', 'tok^2']
    for q in malignas:
        try:
            query(home, q)  # nunca sqlite3.OperationalError
        except QueryError:
            pass  # erro de uso é aceitável; erro de sintaxe FTS não
