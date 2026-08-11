"""tests/test_regime_ranking.py — a cota curada, medida antes e depois.

Corpus gerado, não commitado: 60 grãos espelhados saturam o `_TOPN=50` por
variante, que é exatamente a condição medida na biblioteca viva (em 11 de 21
termos em disputa, nenhum grão curado chega sequer ao pool de candidatos). Um
teste com 3 espelhos não reproduziria o defeito e passaria vazio.

Os dois lados são afirmados no MESMO arquivo: com cota 0 o curado tem que
estar AUSENTE (senão o teste não mede nada) e com a cota default tem que estar
presente — com a cabeça do ranking intacta.
"""
import json

import pytest

from neurata.frontmatter import serialize
from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex

TERMO = "token"


def _escreve(home, slug, meta_extra, body):
    meta = {"id": slug, "title": slug.replace("-", " "),
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    meta.update(meta_extra)
    (home.library / f"{slug}.md").write_text(
        serialize(meta, body), encoding="utf-8")


def _home(tmp_path, quota):
    home = NeurataHome(tmp_path)
    home.init()
    for n in range(60):
        _escreve(home, f"espelho-{n:02d}",
                 {"source_key": f"skill:s{n:02d}",
                  "source_path": f"s{n:02d}/SKILL.md",
                  "title": f"{TERMO} handling {n:02d}"},
                 f"{TERMO} {TERMO} {TERMO} rotacao de {TERMO} caso {n:02d}")
    # Curados: o termo aparece uma vez, no corpo — perdem no BM25 de propósito.
    for n in range(2):
        _escreve(home, f"curado-{n}", {},
                 f"decisao {n} do dono sobre o {TERMO} do projeto")
    home.config_path.write_text(
        json.dumps({"shelf": {"beta": 0}, "regime": {"curated_quota": quota}},
                   indent=2), encoding="utf-8")
    reindex(home)
    return home


def _slugs(home, qstr=TERMO, limit=10):
    return [c["slug"] for c in query(home, qstr, limit=limit)["results"]]


def test_cota_zero_reproduz_o_afogamento(tmp_path):
    slugs = _slugs(_home(tmp_path / "sem", 0))
    assert len(slugs) == 10
    assert not [s for s in slugs if s.startswith("curado-")]


def test_cota_default_traz_curado_sem_mexer_na_cabeca(tmp_path):
    antes = _slugs(_home(tmp_path / "sem", 0))
    depois = _slugs(_home(tmp_path / "com", 3))

    curados = [s for s in depois if s.startswith("curado-")]
    assert len(curados) == 2          # só existem 2; cota 3 não inventa item
    assert depois[-2:] == curados     # entram no rodapé, não no topo
    assert depois[:8] == antes[:8]    # cabeça intacta, item a item
    assert len(depois) == 10


def test_consulta_so_de_espelho_nao_regride(tmp_path):
    """Controle: termo sem nenhum grão curado casando -> saída idêntica."""
    sem = _slugs(_home(tmp_path / "sem", 0), qstr="rotacao")
    com = _slugs(_home(tmp_path / "com", 3), qstr="rotacao")
    assert sem == com


def test_faceta_explicita_desliga_a_cota(tmp_path):
    slugs = _slugs(_home(tmp_path / "com", 3), qstr=f"regime:mirror {TERMO}")
    assert slugs and not [s for s in slugs if s.startswith("curado-")]


def test_card_da_cota_diz_de_onde_veio(tmp_path):
    home = _home(tmp_path / "com", 3)
    cards = query(home, TERMO, limit=10)["results"]
    da_cota = [c for c in cards if c["slug"].startswith("curado-")]
    assert da_cota and all(c["via"] == "curated" for c in da_cota)


@pytest.mark.parametrize("limit,maximo_curado", [(1, 0), (2, 1), (10, 2)])
def test_cota_nunca_toma_mais_que_metade(tmp_path, limit, maximo_curado):
    slugs = _slugs(_home(tmp_path / "com", 3), limit=limit)
    assert len(slugs) == limit
    assert len([s for s in slugs if s.startswith("curado-")]) == maximo_curado
