"""tests/test_providers_claude_code.py — scanner de skills Claude Code (Fase 3)."""
import os

import pytest

from neurata.providers.claude_code import Skill, scan


def _write_skill(base, dirname, frontmatter, body="Corpo da skill.\n"):
    d = base / dirname
    d.mkdir()
    text = "---\n" + frontmatter + "\n---\n" + body
    (d / "SKILL.md").write_text(text, encoding="utf-8")
    return d / "SKILL.md"


def test_scan_reads_skill_with_name_and_description(tmp_path):
    path = _write_skill(
        tmp_path, "foo",
        "name: foo-skill\ndescription: faz coisas de foo",
        body="# Foo\nDo foo things.\n",
    )
    skills, skipped = scan(tmp_path)
    assert skipped == []
    assert skills == [Skill(
        name="foo-skill", description="faz coisas de foo",
        body="# Foo\nDo foo things.\n", source_path=str(path),
    )]


def test_scan_name_fallback_to_dirname_when_missing(tmp_path):
    _write_skill(tmp_path, "bar", "description: sem nome no frontmatter")
    skills, _ = scan(tmp_path)
    assert len(skills) == 1
    assert skills[0].name == "bar"


def test_scan_skips_dir_without_skill_md(tmp_path):
    (tmp_path / "baz").mkdir()
    skills, skipped = scan(tmp_path)
    assert skills == []
    assert len(skipped) == 1
    assert skipped[0].reason == "no SKILL.md"
    assert skipped[0].path.endswith("baz/SKILL.md")


def test_scan_skips_unreadable_skill_md(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("root ignora permissões de arquivo")
    path = _write_skill(tmp_path, "unreadable", "name: x\ndescription: y")
    os.chmod(path, 0o000)
    try:
        skills, skipped = scan(tmp_path)
    finally:
        os.chmod(path, 0o644)
    assert skills == []
    assert len(skipped) == 1
    assert skipped[0].path == str(path)
    assert "unreadable" in skipped[0].reason


def test_scan_skips_unparseable_frontmatter(tmp_path):
    d = tmp_path / "broken"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: sem terminador\n", encoding="utf-8")
    skills, skipped = scan(tmp_path)
    assert skills == []
    assert len(skipped) == 1
    assert skipped[0].path == str(d / "SKILL.md")


def test_scan_missing_dir_returns_empty(tmp_path):
    skills, skipped = scan(tmp_path / "nonexistent")
    assert skills == []
    assert skipped == []
