"""tests/test_retrieval_quality.py — qualidade de recuperação, corpus congelado.

Por que este arquivo existe separado de `test_query.py`: aquele testa a
mecânica (a query roda, o facet filtra, o schema é checado). Este testa se
a busca traz o documento CERTO — a promessa do produto.

Três decisões de método, todas deliberadas:

1. **Corpus congelado.** `tests/fixtures/retrieval_corpus/` é commitado e só
   muda por decisão explícita. Medir contra o arquivo vivo (`~/.neurata`) é
   impossível: ele cresce todo dia, então duas medições em dias diferentes
   medem corpora diferentes. Corpus congelado também é o que garante o
   controle negativo — os termos de controle não entram porque o corpus não
   muda.

2. **Gabarito escrito ANTES da medição.** `QRELS` declara o slug correto de
   cada query. Julgar relevância depois de ver o resultado é como um acerto
   em rank 1 vira "falhou" na leitura.

3. **Assertiva sobre POSIÇÃO, nunca sobre score.** O score é derivado de rank
   (RRF), então não mede relevância: no arquivo real um match exato pontuou
   0.0377 e lixo puro pontuou 0.0395. Qualquer limiar sobre esse número é
   ruído. Posição é binária e é literalmente a promessa ("retrieve at the
   right moment").

O corte é `@1`, não `@3`: as 14 queries do gabarito acertam em rank 1 com
folga hoje, então `@3` daria duas posições de margem que ninguém usa — e um
gate que só dispara na catástrofe não dispara nunca. Se uma query legítima
passar a empatar em rank 1, a decisão é afrouxar ESSA linha com comentário,
não o corte inteiro.

Os defeitos conhecidos e ainda não corrigidos ficam como `xfail(strict=True)`:
a suíte fica verde hoje, e o dia em que alguém consertar o bug o teste passa
a XPASS e quebra o CI — obrigando a remover o marcador. Bug conhecido não
vira bug esquecido.

`shelf.beta = 0` no config do fixture desliga o boost por uso. Sem isso as
queries anteriores mudariam o rank das seguintes (cada `query()` grava
impressão), e a suíte ficaria dependente de ordem — com `pytest-randomly`,
flaky. O boost por uso tem cobertura própria em `test_shelf.py`.
"""
import json
import shutil
from pathlib import Path

import pytest

from neurata.home import NeurataHome
from neurata.query import query
from neurata.reindex import reindex

CORPUS = Path(__file__).parent / "fixtures" / "retrieval_corpus"

# Gabarito: query → slug que DEVE vir em rank 1.
# Escrito antes de rodar qualquer medição. Não editar pra fazer passar —
# se a busca não acha, o defeito é da busca.
QRELS = [
    ("datapipe offline export", "datapipe-0-10-0-update"),
    # Dois documentos falam de datapipe: a query tem que escolher o certo.
    ("datapipe systemd serviço", "datapipe-proxy-systemd"),
    # "merge" e "push" também estão em git-worktree-fluxo; "fichario" decide.
    ("fichario merge push", "fichario-merge-push"),
    ("worktree branch isolada", "git-worktree-fluxo"),
    # "config" também está em gateway-config-model-mx52.
    ("ruff pyproject pyright", "ruff-config-pyproject"),
    ("fallback modelos config global", "gateway-config-model-mx52"),
    ("gate dias distintos dogfooding", "neurata-gate-dogfooding"),
    ("rank recíproco consenso", "neurata-rrf-fusao"),
    ("tokenizer unicode pontuação", "sqlite-fts5-tokenizer"),
    ("restic bucket retenção", "backup-restic-b2"),
    ("timer unit log estruturado", "systemd-timer-vs-cron"),
    ("vault wikilink markdown", "obsidian-vault-layout"),
    ("relay liaison instâncias", "relay-liaison"),
    ("envelope proveniência inbox", "neurata-deposit-envelope"),
]

# Controles negativos: nenhum termo aparece no corpus congelado, e o corpus
# não muda — então nenhum relatório futuro pode contaminá-los (foi assim que
# "bolo de chocolate" morreu como controle no arquivo vivo).
CONTROLES = [
    "kubernetes terraform helm istio",
    "receita bolo chocolate forno",
]


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    """Corpus congelado indexado uma vez. Compartilhado de propósito: com
    `beta=0` o ranking não depende de uso acumulado, então a ordem dos
    testes não pode alterar resultado. Se alterar, é defeito."""
    root = tmp_path_factory.mktemp("retrieval")
    h = NeurataHome(root)
    h.init()
    for src in sorted(CORPUS.glob("*.md")):
        shutil.copy(src, h.library / src.name)
    cfg = (json.loads(h.config_path.read_text(encoding="utf-8"))
           if h.config_path.exists() else {})
    cfg.setdefault("shelf", {})["beta"] = 0
    h.config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    reindex(h)
    return h


def _slugs(home: NeurataHome, qstr: str, limit: int = 10) -> list[str]:
    return [c["slug"] for c in query(home, qstr, limit=limit)["results"]]


def _rank(home: NeurataHome, qstr: str, slug: str) -> "int | None":
    """Posição 1-based do slug, ou None se não veio."""
    slugs = _slugs(home, qstr)
    return slugs.index(slug) + 1 if slug in slugs else None


@pytest.mark.parametrize("qstr,expected", QRELS,
                         ids=[q for q, _ in QRELS])
def test_recall_at_1(home, qstr, expected):
    slugs = _slugs(home, qstr)
    assert slugs[:1] == [expected], (
        f"{qstr!r}: esperado {expected} em rank 1, veio {slugs[:3]}")


@pytest.mark.parametrize("qstr", CONTROLES)
def test_controle_negativo_nao_casa(home, qstr):
    assert _slugs(home, qstr) == [], (
        f"controle negativo sujo: {qstr!r} casou algo — ou o corpus mudou, "
        f"ou termo comum está casando sozinho")


def test_stopword_nao_domina_termo_raro(home):
    """Preposição não pode superar termo raro. Query mista tem que rankear
    pelo termo que discrimina, não pelo que casa tudo.

    Foi defeito aberto, e a causa que estava escrita aqui — "'de' casa o
    corpus inteiro e a fusão trata como voto igual" — era falsa: medido,
    'de' tem df=16 e IDF zerado, e o documento certo era rank 1 em raw,
    norm, singular e prefix. Quem elegia o ruído era a variante `plural`,
    `("notas" OR "des" OR "datapipes")`: nenhuma flexão existia no corpus,
    a lista degenerava em "quem casa notas" e dava voto de rank 1 no RRF a
    um documento que ficava em 3 em toda variante fiel. Corrigido movendo a
    morfologia para dentro da variante `norm` (ver `router.variants`)."""
    slugs = _slugs(home, "notas de datapipe")
    assert slugs[:1] == ["datapipe-0-10-0-update"], (
        f"stopword dominou o termo raro: {slugs[:3]}")


# Os únicos documentos do corpus congelado que contêm o token "datapipe".
_COM_DATAPIPE = {"datapipe-0-10-0-update", "datapipe-proxy-systemd"}


def test_versao_com_ponto_nao_admite_ruido(home):
    """Somar um número de versão a um termo raro pode reordenar, mas não
    pode ADMITIR documento que não contém o termo raro.

    Foi defeito aberto, e a causa que estava escrita aqui — "unicode61 corta
    em não-alfanumérico" — culpava o índice por um bug da query: `"0.10"`
    entre aspas já é FRASE em FTS5 (`0` seguido de `10`), casa `0.10.0` e
    rejeita `3.0`, e a variante `raw` sempre acertou. Quem admitia ruído era
    `norm`, que fazia `normalize(tok).split()` e transformava um termo do
    usuário em dois termos OR'd. Corrigido mantendo o token normalizado como
    termo único; o tokenizer nunca precisou mudar."""
    intrusos = set(_slugs(home, "datapipe 0.10")) - _COM_DATAPIPE
    assert intrusos == set(), f"versão fragmentada admitiu ruído: {intrusos}"


def test_termo_extra_verdadeiro_nao_piora_rank(home):
    """Invariante de fusão: acrescentar termo VERDADEIRO sobre o documento
    alvo nunca pode piorar a posição dele. Se piorar, a fusão está diluindo
    o termo raro em vez de reforçá-lo — foi o que derrubou a busca por
    datapipe no arquivo real (1 termo achava, 5 termos afundava)."""
    alvo = "datapipe-0-10-0-update"
    r1 = _rank(home, "datapipe", alvo)
    r5 = _rank(home, "datapipe 0.10 offline export flash", alvo)
    assert r1 is not None and r5 is not None, (r1, r5)
    assert r5 <= r1, f"5 termos verdadeiros pioraram o rank: {r1} → {r5}"
