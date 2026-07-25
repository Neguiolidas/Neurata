"""tests/test_harvest.py — orquestrador harvest + REGISTRY (Fase 4)."""
import hashlib

import pytest

from neurata.frontmatter import parse
from neurata.harvest import REGISTRY, harvest
from neurata.home import NeurataHome
from neurata.indexdb import IndexSchemaError, connect, drop_schema


def _mkhome(tmp_path):
    home = NeurataHome(tmp_path / "neurata")
    home.init()
    return home


def _write_skill(skills_dir, dirname, name=None, description="desc",
                 body="Corpo.\n"):
    d = skills_dir / dirname
    d.mkdir(parents=True, exist_ok=True)
    fm = f"description: {description}\n"
    if name is not None:
        fm = f"name: {name}\n" + fm
    (d / "SKILL.md").write_text("---\n" + fm + "---\n" + body,
                                encoding="utf-8")


def _inbox_files(home):
    return sorted(home.inbox.glob("*.md"))


def test_harvest_fresh_two_skills(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")
    _write_skill(skills_dir, "b", name="b-skill", body="Corpo B.\n")

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.target == "claude-code"
    assert report.harvested == 2
    assert report.updated == 0
    assert report.removed == 0
    files = _inbox_files(home)
    assert len(files) == 2
    metas = [parse(f.read_text(encoding="utf-8"))[0] for f in files]
    keys = {m["source_key"] for m in metas}
    assert keys == {"claude-code:a-skill", "claude-code:b-skill"}
    for m in metas:
        assert m["type"] == "skill"
        assert m["env"] == "claude-code"
        assert "id" in m and "title" in m and "description" in m
        assert "source_path" in m and "created" in m and "content_hash" in m


def test_harvest_content_hash_matches_body(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")

    harvest(home, "claude-code", skills_dir=skills_dir)

    f = _inbox_files(home)[0]
    meta, body = parse(f.read_text(encoding="utf-8"))
    assert meta["content_hash"] == hashlib.sha256(
        body.encode("utf-8")).hexdigest()


def test_harvest_reharvest_unchanged_emits_nothing(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")
    harvest(home, "claude-code", skills_dir=skills_dir)
    # Simula reindex: entra na library com o mesmo source_key/content_hash.
    f = _inbox_files(home)[0]
    meta, _body = parse(f.read_text(encoding="utf-8"))
    con = connect(home)
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, grain_quality,"
        " shingles, source_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (meta["id"], "a-skill", "library/a.md", "library", "skill",
         "generic", meta["title"], meta["description"],
         meta["content_hash"], meta["created"], meta["created"],
         "mechanical", "[]", meta["source_key"]))
    con.commit()
    f.unlink()

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.harvested == 0
    assert report.updated == 0
    assert _inbox_files(home) == []


def test_harvest_pending_item_not_duplicated(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")
    harvest(home, "claude-code", skills_dir=skills_dir)
    assert len(_inbox_files(home)) == 1

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.harvested == 0
    assert len(_inbox_files(home)) == 1


def test_harvest_changed_skill_emits_update(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")
    _f_meta, _ = parse((skills_dir / "a" / "SKILL.md").read_text())
    con = connect(home)
    body = "Corpo A.\n"
    chash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, grain_quality,"
        " shingles, source_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("01LIB", "a-skill", "library/a.md", "library", "skill", "generic",
         "a-skill", "desc", chash, "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00", "mechanical", "[]",
         "claude-code:a-skill"))
    con.commit()
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A mudou.\n")

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.updated == 1
    assert report.harvested == 0
    files = _inbox_files(home)
    assert len(files) == 1
    _meta, body2 = parse(files[0].read_text(encoding="utf-8"))
    assert body2 == "Corpo A mudou.\n"


def test_harvest_removed_skill_emits_tombstone(tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    _write_skill(skills_dir, "a", name="a-skill", body="Corpo A.\n")
    con = connect(home)
    body = "Corpo A.\n"
    chash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, grain_quality,"
        " shingles, source_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("01LIB", "a-skill", "library/a.md", "library", "skill", "generic",
         "a-skill", "desc", chash, "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00", "mechanical", "[]",
         "claude-code:a-skill"))
    con.commit()
    # Fonte não tem mais a skill 'a'.
    import shutil
    shutil.rmtree(skills_dir)
    skills_dir.mkdir(exist_ok=True)

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.removed == 1
    files = _inbox_files(home)
    assert len(files) == 1
    meta, _ = parse(files[0].read_text(encoding="utf-8"))
    assert meta["type"] == "skill-tombstone"
    assert meta["source_key"] == "claude-code:a-skill"


def test_harvest_removed_skill_tombstone_already_pending_not_duplicated(
        tmp_path):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "skills"
    con = connect(home)
    con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, content_hash, created, updated, grain_quality,"
        " shingles, source_key) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("01LIB", "a-skill", "library/a.md", "library", "skill", "generic",
         "a-skill", "desc", "deadbeef", "2026-01-01T00:00:00+00:00",
         "2026-01-01T00:00:00+00:00", "mechanical", "[]",
         "claude-code:a-skill"))
    con.commit()
    (home.inbox / "01TOMB-a-skill.md").write_text(
        "---\nid: 01TOMB\ntype: skill-tombstone\n"
        "source_key: claude-code:a-skill\n"
        "created: 2026-01-02T00:00:00+00:00\n---\n", encoding="utf-8")

    report = harvest(home, "claude-code", skills_dir=skills_dir)

    assert report.removed == 0
    files = _inbox_files(home)
    assert len(files) == 1  # só o tombstone já existente, nenhum novo


def test_harvest_uses_env_var_for_skills_dir(tmp_path, monkeypatch):
    home = _mkhome(tmp_path)
    skills_dir = tmp_path / "envskills"
    _write_skill(skills_dir, "a", name="a-skill")
    monkeypatch.setenv("NEURATA_CLAUDE_SKILLS_DIR", str(skills_dir))

    report = harvest(home, "claude-code")

    assert report.harvested == 1


def test_harvest_v5_index_requires_reindex(tmp_path):
    home = _mkhome(tmp_path)
    con = connect(home)
    drop_schema(con)
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO meta VALUES ('index_schema_version', '5')")
    con.commit()
    con.close()

    # Tipo exato: schema futuro tem que abortar por versão incompatível,
    # não por um AttributeError qualquer no caminho.
    with pytest.raises(IndexSchemaError):
        harvest(home, "claude-code", skills_dir=tmp_path / "skills")


def test_harvest_unknown_target_raises(tmp_path):
    home = _mkhome(tmp_path)
    with pytest.raises(KeyError):
        harvest(home, "bogus")


def test_registry_has_claude_code():
    assert "claude-code" in REGISTRY
