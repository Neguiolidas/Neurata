"""tests/test_compact.py"""
from neurata.compact import compact
from neurata.expand import expand
from neurata.frontmatter import parse, serialize
from neurata.home import NeurataHome

CORPO = "\n\n".join(
    f"paragrafo {n} com texto suficiente pra compactar." for n in range(6))


def _grao(tmp_path, nome: str, extra: dict) -> tuple:
    home = NeurataHome(tmp_path)
    home.init()
    meta = {"id": nome, "title": nome,
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    meta.update(extra)
    path = home.library / f"{nome}.md"
    path.write_text(serialize(meta, CORPO), encoding="utf-8")
    return home, path


def test_compact_recusa_rebaixar_grao_refinado(tmp_path):
    """A13 — refined continua recusado, mesma mensagem de hoje."""
    home, path = _grao(tmp_path, "r1", {"grain_quality": "refined"})
    antes = path.read_text(encoding="utf-8")

    out = compact(home, "r1")

    assert out["action"] == "refused"
    assert "refined" in out["reason"]
    assert path.read_text(encoding="utf-8") == antes


def test_compact_aceita_grao_curado_mecanico(tmp_path):
    """A recusa é cirúrgica: o caso normal continua compactando."""
    home, path = _grao(tmp_path, "c1", {"grain_quality": "mechanical"})

    out = compact(home, "c1")

    assert out["action"] == "compacted"
    _, corpo = parse(path.read_text(encoding="utf-8"))
    assert len(corpo) < len(CORPO)


def test_compact_aceita_grao_espelhado(tmp_path):
    """v1.4: a recusa de mirror caiu (só `refined` continua recusado)."""
    home, path = _grao(tmp_path, "m1", {"source_key": "skill:a",
                                        "source_path": "a/SKILL.md"})

    out = compact(home, "m1")

    assert out["action"] == "compacted"
    assert out["archived"]
    _, corpo = parse(path.read_text(encoding="utf-8"))
    assert len(corpo) < len(CORPO)


def test_compact_preserva_updated_do_grao(tmp_path):
    """Compactar troca a representação, não o que o grão diz.

    Carimbar `updated` zerava a idade do acervo inteiro de uma vez, e o
    shelf pontua recência: os grãos recém-compactados subiam no ranking e
    empurravam material intacto — inclusive curado — para fora do topo.
    """
    home, path = _grao(tmp_path, "m1", {"source_key": "skill:a",
                                        "source_path": "a/SKILL.md"})
    antes, _ = parse(path.read_text(encoding="utf-8"))

    assert compact(home, "m1")["action"] == "compacted"

    depois, corpo = parse(path.read_text(encoding="utf-8"))
    assert depois["updated"] == antes["updated"]
    assert len(corpo) < len(CORPO)          # compactou de fato
    assert depois["derived_from"]           # e é rastreável sem a data


def test_compact_expand_restore_espelho_e_byte_a_byte(tmp_path):
    """A12 — compact seguido de expand --restore devolve o corpo do
    espelho exatamente como era antes de compactar."""
    home, path = _grao(tmp_path, "m2", {"source_key": "skill:a",
                                        "source_path": "a/SKILL.md"})

    compacted = compact(home, "m2")
    assert compacted["action"] == "compacted"
    _, summary_corpo = parse(path.read_text(encoding="utf-8"))
    assert summary_corpo != CORPO

    full = expand(home, "m2", grain="full")
    assert full["text"] == CORPO

    restored = expand(home, "m2", restore=True)
    assert restored["action"] == "restored"
    _, corpo_restaurado = parse(path.read_text(encoding="utf-8"))
    assert corpo_restaurado == CORPO


def test_compact_reindex_after_false_nao_reindexa(tmp_path, monkeypatch):
    home, _ = _grao(tmp_path, "n1", {"grain_quality": "mechanical"})
    chamadas = []
    monkeypatch.setattr("neurata.compact.reindex", chamadas.append)

    out = compact(home, "n1", reindex_after=False)

    assert out["action"] == "compacted"
    assert chamadas == []


def test_compact_reindex_after_default_true_reindexa(tmp_path, monkeypatch):
    """O default preserva o comportamento de hoje: o CLI manual reindexa."""
    home, _ = _grao(tmp_path, "n2", {"grain_quality": "mechanical"})
    chamadas = []
    monkeypatch.setattr("neurata.compact.reindex", chamadas.append)

    out = compact(home, "n2")

    assert out["action"] == "compacted"
    assert chamadas == [home]


def test_compact_duas_vezes_e_ponto_fixo_nao_duplica_blob(tmp_path):
    """Idempotência: o resumo do corpo já compactado não encolhe mais
    nada, então a segunda chamada é noop — nada novo vai pro archive,
    nada corrompe."""
    home, path = _grao(tmp_path, "i1", {"grain_quality": "mechanical"})

    primeiro = compact(home, "i1")
    assert primeiro["action"] == "compacted"
    blobs_apos_primeiro = sorted(home.archive.rglob("*"))
    conteudo_apos_primeiro = path.read_text(encoding="utf-8")

    segundo = compact(home, "i1")

    assert segundo["action"] == "noop"
    assert path.read_text(encoding="utf-8") == conteudo_apos_primeiro
    assert sorted(home.archive.rglob("*")) == blobs_apos_primeiro


def test_compact_nao_infla_corpo_de_headings(tmp_path):
    """O guarda é "encolheu?", não "é ponto fixo?".

    Corpo só de headings: `make_summary` junta blocos com "\\n\\n" onde o
    original tinha "\\n", devolvendo um texto MAIOR. Antes, isso não era
    ponto fixo, passava no teste de igualdade e era gravado como
    "compactação" — o corpo servido crescia.
    """
    corpo = "# Alpha\n# Beta\n# Gama\n"
    home = NeurataHome(tmp_path)
    home.init()
    meta = {"id": "h1", "title": "h1", "grain_quality": "mechanical",
            "created": "2026-08-10T00:00:00+00:00",
            "updated": "2026-08-10T00:00:00+00:00"}
    path = home.library / "h1.md"
    path.write_text(serialize(meta, corpo), encoding="utf-8")
    antes = path.read_text(encoding="utf-8")

    from neurata.grains import make_summary
    assert len(make_summary(corpo)) > len(corpo)  # a armadilha existe
    blobs_antes = sorted(home.archive.rglob("*"))

    out = compact(home, "h1")

    assert out["action"] == "noop"
    assert path.read_text(encoding="utf-8") == antes
    assert sorted(home.archive.rglob("*")) == blobs_antes


def test_compact_com_indice_travado_reporta_pendencia_nao_falha(
        tmp_path, monkeypatch):
    """Lock do índice preso: a compactação em disco JÁ aconteceu.

    Reportar erro faria o operador repetir um trabalho feito; o retorno
    diz que só o índice ficou para trás.
    """
    from neurata.indexdb import LockHeldError

    home, path = _grao(tmp_path, "l1", {"grain_quality": "mechanical"})

    def travado(_home):
        raise LockHeldError("índice ocupado")

    monkeypatch.setattr("neurata.compact.reindex", travado)

    out = compact(home, "l1", reindex_after=True)

    assert out["action"] == "compacted-pending-index"
    assert out["archived"]
    assert "reindex" in out["reason"]
    _, corpo = parse(path.read_text(encoding="utf-8"))
    assert len(corpo) < len(CORPO)  # o arquivo foi compactado de fato


def test_compact_com_path_conhecido_nao_varre_o_acervo(tmp_path, monkeypatch):
    """O atalho é o ponto da mudança: com `path`, `resolve` (O(acervo),
    ~0,2 s nos 15 mil arquivos reais) não deve nem ser chamado."""
    home, path = _grao(tmp_path, "p1", {"grain_quality": "mechanical"})

    def proibido(*a, **kw):
        raise AssertionError("resolve varreu o acervo apesar do path")

    monkeypatch.setattr("neurata.compact.resolve", proibido)

    out = compact(home, "p1", reindex_after=False, path=path)

    assert out["action"] == "compacted"


def test_compact_com_path_mentiroso_cai_no_resolve(tmp_path):
    """A linha do índice é dica, não verdade. Path que não existe mais
    (arquivo movido/renomeado depois do SELECT) não pode virar erro: o
    disco decide."""
    home, real = _grao(tmp_path, "p2", {"grain_quality": "mechanical"})
    fantasma = home.library / "sumiu-entre-o-select-e-agora.md"

    out = compact(home, "p2", reindex_after=False, path=fantasma)

    assert out["action"] == "compacted"
    assert out["path"] == str(real.relative_to(home.root))


def test_compact_ignora_path_fora_do_home(tmp_path):
    """Índice velho (ou adulterado) apontando pra fora de library/inbox
    não pode virar entrada pelo atalho e escapar do domínio que
    `resolve` varre: o arquivo de fora fica intacto e quem compacta é o
    grão de dentro."""
    home, dentro = _grao(tmp_path, "p3", {"grain_quality": "mechanical"})
    fora = tmp_path.parent / "fora-do-home.md"
    fora.write_text(
        serialize({"id": "p3", "title": "p3",
                   "created": "2026-08-10T00:00:00+00:00",
                   "updated": "2026-08-10T00:00:00+00:00"}, CORPO),
        encoding="utf-8")
    intacto = fora.read_text(encoding="utf-8")
    try:
        out = compact(home, "p3", reindex_after=False, path=fora)

        assert out["path"] == str(dentro.relative_to(home.root))
        assert fora.read_text(encoding="utf-8") == intacto
    finally:
        fora.unlink()
