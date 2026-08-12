"""tests/test_query.py — end-to-end do pipeline."""
import contextlib

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
        # QueryError (erro de uso) é aceitável; o que este teste proíbe é
        # sqlite3.OperationalError — sintaxe FTS vazando pro usuário.
        with contextlib.suppress(QueryError):
            query(home, q)


# ---- v1.1: facetas de procedência + missing: ----------------------------

def _setup_prov(tmp_path):
    """Fixture controlada: 2 curados com agente, 1 curado sem, 1 mirror.

    O mirror existe para provar que ele nunca entra numa faceta de
    procedência — as colunas dele são NULL por construção.
    """
    home = _home(tmp_path)
    _write(home, "grao-a",
           ["id: 02A", "title: Grão A", "source:", "  agent: hermes",
            "  session: s-1", "  origin: manual"],
           "Deploy do serviço com RRF.\n")
    _write(home, "grao-b",
           ["id: 02B", "title: Grão B", "source:", "  agent: hermes",
            "  session: s-2", "  origin: auto"],
           "Deploy do serviço com BM25.\n")
    _write(home, "grao-sem-agente",
           ["id: 02C", "title: Grão C", "source:", "  origin: manual"],
           "Deploy do serviço sem agente.\n")
    # espelho: `source_key` é o que define o regime — e o `source:` dele,
    # se houver, é do autor original, não uma procedência de depósito.
    _write(home, "espelho",
           ["id: 02D", "title: Espelho", "source_key: claude-code:x",
            "source:", "  agent: hermes"],
           "Deploy do serviço espelhado.\n")
    reindex(home)
    return home


def _slugs(res):
    return {c["slug"] for c in res["results"]}


def test_agent_facet_filters_curated_only(tmp_path):
    res = query(_setup_prov(tmp_path), "agent:hermes")
    assert _slugs(res) == {"grao-a", "grao-b"}


def test_provenance_facet_never_surfaces_mirror(tmp_path):
    home = _setup_prov(tmp_path)
    assert _slugs(query(home, "deploy agent:hermes")) == {"grao-a", "grao-b"}


def test_agent_with_mirror_regime_is_empty_not_error(tmp_path):
    res = query(_setup_prov(tmp_path), "agent:hermes regime:mirror")
    assert res["results"] == []


def test_session_and_origin_facets(tmp_path):
    home = _setup_prov(tmp_path)
    assert _slugs(query(home, "session:s-2")) == {"grao-b"}
    assert _slugs(query(home, "origin:manual")) == {"grao-a",
                                                    "grao-sem-agente"}


def test_missing_agent_lists_curated_gaps(tmp_path):
    res = query(_setup_prov(tmp_path), "missing:agent")
    assert _slugs(res) == {"grao-sem-agente"}


def test_missing_partitions_the_curated_set(tmp_path):
    """|missing:agent| + |agent:*| == |curados|, sem sobra nem sobreposição."""
    home = _setup_prov(tmp_path)
    com = _slugs(query(home, "agent:hermes"))
    sem = _slugs(query(home, "missing:agent"))
    assert com.isdisjoint(sem)
    assert com | sem == {"grao-a", "grao-b", "grao-sem-agente"}


def test_missing_unknown_key_errors_listing_valid(tmp_path):
    with pytest.raises(QueryError) as exc:
        query(_setup_prov(tmp_path), "missing:xpto")
    msg = str(exc.value)
    assert "xpto" in msg
    for key in ("agent", "session", "origin"):
        assert key in msg


def test_missing_with_mirror_regime_errors(tmp_path):
    """Mirror não tem procedência: pedir a lacuna dele é erro de uso,
    não uma lista vazia que o usuário leria como 'está tudo curado'."""
    with pytest.raises(QueryError):
        query(_setup_prov(tmp_path), "missing:agent regime:mirror")


def test_missing_alone_is_not_empty_query(tmp_path):
    res = query(_setup_prov(tmp_path), "missing:origin")
    assert res["results"] == []  # todo curado tem origin nesta fixture


def test_empty_query_message_lists_new_facets(tmp_path):
    with pytest.raises(QueryError) as exc:
        query(_home(tmp_path), "   ")
    assert "agent:" in str(exc.value)
