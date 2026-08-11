"""tests/test_compact.py"""
from neurata.compact import compact
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
    home, path = _grao(tmp_path, "r1", {"grain_quality": "refined"})
    antes = path.read_text(encoding="utf-8")

    out = compact(home, "r1")

    assert out["action"] == "refused"
    assert "refined" in out["reason"]
    assert path.read_text(encoding="utf-8") == antes


def test_compact_recusa_grao_espelhado(tmp_path):
    home, path = _grao(tmp_path, "m1", {"source_key": "skill:a",
                                        "source_path": "a/SKILL.md"})
    antes = path.read_text(encoding="utf-8")

    out = compact(home, "m1")

    assert out["action"] == "refused"
    assert "tick" in out["reason"]
    assert path.read_text(encoding="utf-8") == antes


def test_compact_aceita_grao_curado_mecanico(tmp_path):
    """A recusa é cirúrgica: o caso normal continua compactando."""
    home, path = _grao(tmp_path, "c1", {"grain_quality": "mechanical"})

    out = compact(home, "c1")

    assert out["action"] == "compacted"
    _, corpo = parse(path.read_text(encoding="utf-8"))
    assert len(corpo) < len(CORPO)
