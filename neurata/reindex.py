"""neurata/reindex.py — rebuild total do índice a partir dos arquivos.

2 passes: (1) entries + FTS + tags + grains, guardando mapas
slug/title/alias em memória; (2) resolução de [[wikilinks]] → edges. Link
não-resolvido ou ambíguo é descartado e contado — determinístico, sem
escolha arbitrária.

Grãos são regenerados incrementalmente: snapshot da tabela `grains` antes
do drop, comparado por src_hash contra o corpo atual. Hash bate → reusa
texto (evita custo de re-derivar toda entrada intocada). Regra anti
summary-de-summary: `derived_from` no frontmatter (corpo já compactado)
→ summary = corpo verbatim, sem re-derivar.
"""
import hashlib
import json
import re
import sqlite3
import time
from datetime import datetime, timezone

from neurata.frontmatter import FrontmatterError, parse
from neurata.grains import make_card, make_summary
from neurata.home import NeurataHome
from neurata.indexdb import (INDEX_SCHEMA_VERSION, IndexLock, connect,
                             create_schema, drop_schema)
from neurata.textnorm import normalize

_WIKILINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_AMBIG = -1  # sentinela: título/alias casando 2+ entries


def reindex(home: NeurataHome) -> dict:
    start = time.monotonic()
    skipped: list[dict] = []
    indexed = edges = unresolved = ambiguous = 0
    by_slug: dict[str, int] = {}
    by_title: dict[str, int] = {}
    by_alias: dict[str, int] = {}
    bodies: dict[int, str] = {}
    with IndexLock(home):
        con = connect(home)
        try:
            old_grains = _grains_snapshot(con)
            drop_schema(con)
            create_schema(con)
            for location, base in (("library", home.library),
                                   ("inbox", home.inbox)):
                for path in sorted(base.rglob("*.md")):
                    rel = str(path.relative_to(home.root))
                    try:
                        text = path.read_text(encoding="utf-8")
                    except OSError:
                        skipped.append({"path": rel, "reason": "unreadable"})
                        continue
                    except UnicodeDecodeError:
                        skipped.append({"path": rel, "reason": "unparseable"})
                        continue
                    try:
                        meta, body = parse(text)
                    except FrontmatterError:
                        skipped.append({"path": rel, "reason": "unparseable"})
                        continue
                    if not meta.get("id"):
                        skipped.append({"path": rel, "reason": "missing-id"})
                        continue
                    slug = path.stem
                    reason = _collision(con, str(meta["id"]), slug)
                    if reason:
                        skipped.append({"path": rel, "reason": reason})
                        continue
                    rowid = _insert(con, meta, body, rel, location, slug,
                                     old_grains)
                    indexed += 1
                    by_slug[slug] = rowid
                    _map_put(by_title, str(meta.get("title", slug)).lower(),
                             rowid)
                    for alias in _aliases(meta):
                        _map_put(by_alias, alias.lower(), rowid)
                    bodies[rowid] = body
            for src in sorted(bodies):
                for target in _link_targets(bodies[src]):
                    dst = _resolve(target, by_slug, by_title, by_alias)
                    if dst is None:
                        unresolved += 1
                    elif dst == _AMBIG:
                        ambiguous += 1
                    elif dst != src:
                        cur = con.execute(
                            "INSERT OR IGNORE INTO edges VALUES (?,?)",
                            (src, dst))
                        edges += cur.rowcount
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('last_reindex', ?)",
                (now,))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('skipped', ?)",
                        (json.dumps(skipped),))
            con.execute("INSERT OR REPLACE INTO meta VALUES "
                        "('index_schema_version', ?)",
                        (str(INDEX_SCHEMA_VERSION),))
            con.commit()
        finally:
            con.close()
    return {"indexed": indexed, "skipped": skipped, "edges": edges,
            "unresolved_links": unresolved, "ambiguous_links": ambiguous,
            "duration_ms": int((time.monotonic() - start) * 1000)}


def _collision(con: sqlite3.Connection, eid: str, slug: str) -> "str | None":
    if con.execute("SELECT 1 FROM entries WHERE id=?", (eid,)).fetchone():
        return "id-collision"
    if con.execute("SELECT 1 FROM entries WHERE slug=?", (slug,)).fetchone():
        return "slug-collision"
    return None


def _aliases(meta: dict) -> list[str]:
    v = meta.get("aliases", [])
    if isinstance(v, list):
        return [str(a) for a in v if str(a).strip()]
    return [str(v)] if str(v).strip() else []


def _map_put(m: dict, key: str, rowid: int) -> None:
    if key in m and m[key] != rowid:
        m[key] = _AMBIG
    else:
        m[key] = rowid


def _link_targets(body: str) -> list[str]:
    out = []
    for m in _WIKILINK.finditer(body):
        # sintaxe Obsidian: [[alvo|display]] e [[alvo#heading]]
        t = m.group(1).split("|")[0].split("#")[0].strip()
        if t:
            out.append(t)
    return out


def _resolve(target: str, by_slug: dict, by_title: dict,
             by_alias: dict) -> "int | None":
    if target in by_slug:
        return by_slug[target]
    t = target.lower()
    if t in by_title:
        return by_title[t]
    if t in by_alias:
        return by_alias[t]
    return None


def _grains_snapshot(con: sqlite3.Connection) -> dict:
    """{entry_id: {kind: (text, src_hash)}} da tabela grains atual."""
    snap: dict = {}
    try:
        rows = con.execute(
            "SELECT entry_id, kind, text, src_hash FROM grains").fetchall()
    except sqlite3.OperationalError:
        return snap  # tabela ainda não existe (schema pré-v0.3)
    for eid, kind, text, src_hash in rows:
        snap.setdefault(eid, {})[kind] = (text, src_hash)
    return snap


def _insert(con: sqlite3.Connection, meta: dict, body: str, rel: str,
            location: str, slug: str, old_grains: dict) -> int:
    title = str(meta.get("title", slug))
    tags = meta.get("tags", [])
    tag_list = [str(t) for t in tags] if isinstance(tags, list) else [str(tags)]
    tag_list = [t for t in tag_list if t.strip()]
    tags_text = " ".join(tag_list)
    aliases_text = " ".join(_aliases(meta))
    grain_quality = str(meta.get("grain_quality", "mechanical")) or "mechanical"
    cur = con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, project, content_hash, created, updated,"
        " grain_quality)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(meta["id"]), slug, rel, location,
         str(meta.get("type", "note")), str(meta.get("env", "generic")),
         title, str(meta.get("description", "")),
         meta.get("project"), str(meta.get("content_hash", "")),
         str(meta.get("created", "")), str(meta.get("updated",
                                                    meta.get("created", ""))),
         grain_quality))
    rowid = cur.lastrowid
    con.execute(
        "INSERT INTO entries_fts(rowid, title, aliases, tags, body,"
        " title_norm, aliases_norm, tags_norm, body_norm)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (rowid, title, aliases_text, tags_text, body, normalize(title),
         normalize(aliases_text), normalize(tags_text), normalize(body)))
    for tag in {t.lower() for t in tag_list}:
        con.execute("INSERT OR IGNORE INTO entry_tags VALUES (?,?)",
                    (rowid, tag))
    _write_grains(con, str(meta["id"]), meta, body, old_grains)
    return rowid


def _write_grains(con: sqlite3.Connection, entry_id: str, meta: dict,
                   body: str, old_grains: dict) -> None:
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    prior = old_grains.get(entry_id, {})

    card_prev = prior.get("card")
    if card_prev and card_prev[1] == body_hash:
        card_text = card_prev[0]
    else:
        card_text = make_card(meta, body)

    if meta.get("derived_from"):
        # Regra anti summary-de-summary: corpo já compactado é o summary.
        summary_text = body
    else:
        summary_prev = prior.get("summary")
        if summary_prev and summary_prev[1] == body_hash:
            summary_text = summary_prev[0]
        else:
            summary_text = make_summary(body)

    con.execute(
        "INSERT INTO grains VALUES (?,?,?,?)",
        (entry_id, "card", card_text, body_hash))
    con.execute(
        "INSERT INTO grains VALUES (?,?,?,?)",
        (entry_id, "summary", summary_text, body_hash))
