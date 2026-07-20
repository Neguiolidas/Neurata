"""neurata/harvest.py — orquestrador: providers externos → itens no inbox.

`harvest(home, target)` resolve o `skills_dir` da fonte (env
`NEURATA_CLAUDE_SKILLS_DIR` ou `~/.claude/skills` p/ "claude-code"),
chama `provider.scan`, compara contra o que já está na library
(`source_key`) e contra o que já está pendente no inbox (evita
duplicar), e emite itens novos/atualizados + tombstones pro que sumiu
da fonte. Read-only no índice: nenhum INSERT/UPDATE em `entries` —
harvest só escreve `.md` no inbox; quem indexa é o `tick`/`reindex`.
"""
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

from neurata.frontmatter import FrontmatterError, parse, serialize
from neurata.home import NeurataHome
from neurata.indexdb import check_schema, connect
from neurata.providers import claude_code
from neurata.ulid import new_ulid

REGISTRY = {"claude-code": claude_code}

_DEFAULT_SKILLS_DIR_ENV = "NEURATA_CLAUDE_SKILLS_DIR"
_DEFAULT_SKILLS_DIR = "~/.claude/skills"


@dataclass
class HarvestReport:
    target: str
    harvested: int = 0
    updated: int = 0
    removed: int = 0
    skipped: list = field(default_factory=list)


def _default_skills_dir() -> Path:
    raw = os.environ.get(_DEFAULT_SKILLS_DIR_ENV) or _DEFAULT_SKILLS_DIR
    return Path(raw).expanduser()


def harvest(home: NeurataHome, target: str,
           skills_dir: "Path | None" = None) -> HarvestReport:
    provider = REGISTRY[target]
    if skills_dir is None:
        skills_dir = _default_skills_dir()

    con = connect(home)
    try:
        check_schema(con, require_reindexed=False)

        known: dict = {}
        known_paths: dict = {}
        for sk, chash, path_str in con.execute(
                "SELECT source_key, content_hash, path FROM entries"
                " WHERE source_key LIKE ? AND location='library'",
                (f"{target}:%",)).fetchall():
            known[sk] = chash
            known_paths[sk] = path_str
    finally:
        con.close()

    pending, pending_tombstones = _scan_inbox_pending(home, target)

    skills, skipped = provider.scan(skills_dir)
    report = HarvestReport(target=target, skipped=list(skipped))

    scanned_keys: set = set()
    for skill in skills:
        source_key = f"{target}:{skill.name}"
        scanned_keys.add(source_key)
        body_hash = hashlib.sha256(skill.body.encode("utf-8")).hexdigest()
        if known.get(source_key) == body_hash:
            continue
        if pending.get(source_key) == body_hash:
            continue
        _emit_item(home, target, skill, source_key, body_hash)
        if source_key in known:
            report.updated += 1
        else:
            report.harvested += 1

    removed_keys = (set(known) | set(pending)) - scanned_keys
    for source_key in sorted(removed_keys):
        if source_key in pending_tombstones:
            continue
        if _is_already_stale(home, known_paths.get(source_key)):
            continue
        _emit_tombstone(home, source_key)
        report.removed += 1

    return report


def _is_already_stale(home: NeurataHome, rel_path: "str | None") -> bool:
    """True se a entry da library em `rel_path` já está marcada stale.

    Evita reemitir tombstone (e inflar `report.removed`) pra uma entry que
    um tick anterior já processou como stale — o skill sumiu da fonte uma
    vez, o tombstone rodou, e nada mudou desde então. `rel_path` é None
    quando o source_key só existe em `pending` (item ainda não indexado);
    nesse caso não há arquivo de library pra checar.
    """
    if not rel_path:
        return False
    path = home.root / rel_path
    try:
        text = path.read_text(encoding="utf-8")
        meta, _body = parse(text)
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return False
    return str(meta.get("stale", "")).lower() == "true"


def _scan_inbox_pending(home: NeurataHome,
                        target: str) -> "tuple[dict, set]":
    prefix = f"{target}:"
    pending: dict = {}
    pending_tombstones: set = set()
    if not home.inbox.is_dir():
        return pending, pending_tombstones
    for path in sorted(home.inbox.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            meta, body = parse(text)
        except (OSError, UnicodeDecodeError, FrontmatterError):
            continue
        source_key = meta.get("source_key")
        if not source_key or not str(source_key).startswith(prefix):
            continue
        source_key = str(source_key)
        if meta.get("type") == "skill-tombstone":
            pending_tombstones.add(source_key)
        else:
            content_hash = meta.get("content_hash") or hashlib.sha256(
                body.encode("utf-8")).hexdigest()
            pending[source_key] = content_hash
    return pending, pending_tombstones


def _emit_item(home: NeurataHome, target: str, skill, source_key: str,
               body_hash: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry_id = new_ulid()
    meta = {
        "id": entry_id,
        "type": "skill",
        "env": target,
        "title": skill.name,
        "description": skill.description,
        "source_key": source_key,
        "source_path": skill.source_path,
        "created": now,
        "content_hash": body_hash,
    }
    from neurata.textnorm import slugify
    slug = slugify(skill.name)
    path = home.inbox / f"{entry_id}-{slug}.md"
    path.write_text(serialize(meta, skill.body), encoding="utf-8")


def _emit_tombstone(home: NeurataHome, source_key: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entry_id = new_ulid()
    meta = {
        "id": entry_id,
        "type": "skill-tombstone",
        "source_key": source_key,
        "created": now,
    }
    slug = source_key.replace(":", "-").replace("/", "-")
    path = home.inbox / f"{entry_id}-{slug}-tombstone.md"
    path.write_text(serialize(meta, ""), encoding="utf-8")
