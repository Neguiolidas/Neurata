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


def test_compact_recusa_grao_espelhado(tmp_path):
    """Espelho tem dono: a fonte. O próximo tick reescreve o corpo a partir
    dela e `_sync_update_in_place` derruba o `derived_from` junto — a
    compactação sumiria sem aviso e o blob ficaria órfão no archive.
    Recusar na hora é honesto; aceitar é trabalho que evapora."""
    home, path = _grao(tmp_path, "m1", {"source_key": "skill:a",
                                        "source_path": "a/SKILL.md"})
    antes = path.read_text(encoding="utf-8")

    out = compact(home, "m1")

    assert out["action"] == "refused"
    assert "tick" in out["reason"]
    assert path.read_text(encoding="utf-8") == antes  # nada tocado


def test_compact_preserva_updated_do_grao(tmp_path):
    """Compactar troca a representação, não o que o grão diz.

    Carimbar `updated` zerava a idade do acervo inteiro de uma vez, e o
    shelf pontua recência: os grãos recém-compactados subiam no ranking e
    empurravam material intacto — inclusive curado — para fora do topo.
    """
    home, path = _grao(tmp_path, "m1", {"grain_quality": "mechanical"})
    antes, _ = parse(path.read_text(encoding="utf-8"))

    assert compact(home, "m1")["action"] == "compacted"

    depois, corpo = parse(path.read_text(encoding="utf-8"))
    assert depois["updated"] == antes["updated"]
    assert len(corpo) < len(CORPO)          # compactou de fato
    assert depois["derived_from"]           # e é rastreável sem a data


def test_compact_expand_restore_e_byte_a_byte(tmp_path):
    """A12 — compact seguido de expand --restore devolve o corpo
    exatamente como era antes de compactar."""
    home, path = _grao(tmp_path, "m2", {"grain_quality": "mechanical"})

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



def test_compact_reindexa_o_indice(tmp_path, monkeypatch):
    """Compactar troca o corpo servido: sem reindexar, a busca continua
    devolvendo o texto velho até o próximo tick."""
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

    out = compact(home, "l1")

    assert out["action"] == "compacted-pending-index"
    assert out["archived"]
    assert "reindex" in out["reason"]
    _, corpo = parse(path.read_text(encoding="utf-8"))
    assert len(corpo) < len(CORPO)  # o arquivo foi compactado de fato




