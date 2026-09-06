"""tests/test_assertion.py — extração determinística de afirmações (v1.5).

O exemplo-canônico do dono governa: "Use X" e "nunca use X" têm Jaccard
baixíssimo (quase-duplicata nunca os pega) e têm que chegar aqui como a
MESMA afirmação (X) com polaridades opostas.
"""
from neurata.assertion import Assertion, extract, find_pairs


def _targets_pol(body):
    return [(a.target, a.polarity) for a in extract(body)]


def test_use_x_pos():
    assert _targets_pol("Use o Postgres para produção.") == [
        ("postgres", "pos")]


def test_nunca_use_x_neg_mesmo_alvo():
    """O coração da v1.5: negação muda polaridade, NUNCA o alvo."""
    assert _targets_pol("Nunca use postgres em produção.") == [
        ("postgres", "neg")]


def test_par_do_roadmap_emparelhavel():
    pos = extract("Use sqlite para o cache local.")
    neg = extract("Nunca use sqlite para o cache local. Ele corrompe.")
    assert [(a.target, a.polarity) for a in pos] == [("sqlite", "pos")]
    assert next((a.target, a.polarity) for a in neg) == ("sqlite", "neg")


def test_evite_e_avoid_sao_neg():
    assert _targets_pol("Evite redis no staging.") == [("redis", "neg")]
    assert _targets_pol("Avoid regex for HTML parsing.") == [
        ("regex", "neg")]


def test_conectivo_corta_alvo():
    """O que vem depois de condição não é objeto: "use o cache SE a
    leitura dominar" tem alvo "cache", não "cache se a leitura"."""
    assert _targets_pol("Use o cache se a leitura dominar.") == [
        ("cache", "pos")]


def test_artigo_a_esquerda_nao_e_identidade():
    assert _targets_pol("Use o Postgres.") == _targets_pol("Use Postgres.")


def test_multiplas_afirmacoes_uma_sentenca():
    body = "Use sqlite para o cache; evite redis."
    got = _targets_pol(body)
    assert ("sqlite", "pos") in got
    assert ("redis", "neg") in got


def test_prosa_descritiva_sem_predicado_vazia():
    assert extract("O Postgres é um banco relacional. Cache ajuda.") == []


def test_corpo_curto_ou_vazio():
    assert extract("") == []
    assert extract("use") == []  # sem objeto


def test_alvo_minimo_e_cap_de_tokens():
    assert _targets_pol("Use ab.") == []  # < 3 chars
    got = _targets_pol("Use alpha beta gamma delta epsilon zeta eta "
                       "theta em producao.")  # cap 6 tokens
    assert got == [("alpha beta gamma delta epsilon zeta", "pos")]


def test_quote_guarda_sentenca_para_journal():
    [a] = extract("Nunca habilite SSL na porta local.")
    assert isinstance(a, Assertion)
    assert "Nunca habilite SSL" in a.quote


def test_determinismo_bit_a_bit():
    body = "Use sqlite. Prefira WAL. Evite redis em prod."
    assert [a.target for a in extract(body)] == [
        a.target for a in extract(body)]


def test_find_pairs_so_polaridade_oposta():
    known = {"cache": [("01A", "neg"), ("01B", "pos")]}
    novas = [Assertion("cache", "pos", "use cache"),
             Assertion("wal", "pos", "use wal")]
    pares = find_pairs(novas, known)
    assert pares == [("01A", "cache", "pos")]  # 01B concorda, wal é órfã
