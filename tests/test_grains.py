"""tests/test_grains.py"""
from neurata.grains import make_card, make_summary


def test_card_usa_description():
    card = make_card({"description": "Uma linha: o que é, quando usar."},
                     "Corpo longo irrelevante.")
    assert card == "Uma linha: o que é, quando usar."


def test_card_fallback_primeira_frase():
    card = make_card({}, "Primeira frase do corpo. Segunda frase ignorada.")
    assert card == "Primeira frase do corpo."


def test_card_fallback_pula_heading():
    card = make_card({}, "# Título\n\nA frase real vem aqui. Resto.")
    assert card == "A frase real vem aqui."


def test_card_cap_140():
    card = make_card({}, "x" * 300)
    assert len(card) == 140
    assert card.endswith("…")


def test_card_vazio_para_corpo_vazio():
    assert make_card({}, "") == ""


def test_summary_headings_mais_primeiro_paragrafo():
    body = ("Preâmbulo primeiro parágrafo.\nSegunda linha do mesmo.\n\n"
            "Preâmbulo segundo parágrafo — fora do summary.\n\n"
            "# Seção A\n\nParágrafo A1.\n\nParágrafo A2 fora.\n\n"
            "## Seção B\n\nParágrafo B1.\n")
    s = make_summary(body)
    assert "Preâmbulo primeiro parágrafo." in s
    assert "segundo parágrafo — fora" not in s
    assert "# Seção A" in s and "Parágrafo A1." in s
    assert "Parágrafo A2 fora." not in s
    assert "## Seção B" in s and "Parágrafo B1." in s


def test_summary_sem_headings_dois_paragrafos():
    body = "Um.\n\nDois.\n\nTrês.\n"
    s = make_summary(body)
    assert "Um." in s and "Dois." in s and "Três." not in s


def test_summary_heading_dentro_de_fence_ignorado():
    body = ("# Real\n\nTexto.\n\n```md\n# fake heading\ncodigo\n```\n\n"
            "# Outra\n\nMais texto.\n")
    s = make_summary(body)
    assert "# Real" in s and "# Outra" in s
    assert "# fake heading" not in s


def test_summary_heading_sem_paragrafo():
    s = make_summary("# Só título\n\n# Segundo\n\nCorpo.\n")
    assert "# Só título" in s
    assert "# Segundo" in s and "Corpo." in s


def test_summary_vazio():
    assert make_summary("") == ""


def test_deterministico():
    body = "# A\n\nP1.\n\nP2.\n"
    assert make_summary(body) == make_summary(body)
    assert make_card({}, body) == make_card({}, body)
