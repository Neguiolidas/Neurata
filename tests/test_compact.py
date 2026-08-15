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
    """Idempotência: make_summary(summary) == summary, então a segunda
    chamada é noop — nada novo vai pro archive, nada corrompe."""
    home, path = _grao(tmp_path, "i1", {"grain_quality": "mechanical"})

    primeiro = compact(home, "i1")
    assert primeiro["action"] == "compacted"
    blobs_apos_primeiro = sorted(home.archive.rglob("*"))
    conteudo_apos_primeiro = path.read_text(encoding="utf-8")

    segundo = compact(home, "i1")

    assert segundo["action"] == "noop"
    assert path.read_text(encoding="utf-8") == conteudo_apos_primeiro
    assert sorted(home.archive.rglob("*")) == blobs_apos_primeiro
