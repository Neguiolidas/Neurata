"""armarium/reindex.py — rebuild total do índice a partir dos arquivos."""
import json
import sqlite3
import time
from datetime import datetime, timezone

from armarium.frontmatter import FrontmatterError, parse
from armarium.home import ArmariumHome
from armarium.indexdb import IndexLock, connect, create_schema, drop_schema
from armarium.textnorm import normalize


def reindex(home: ArmariumHome) -> dict:
    start = time.monotonic()
    skipped: list[dict] = []
    indexed = 0
    with IndexLock(home):
        con = connect(home)
        try:
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
                    try:
                        _insert(con, meta, body, rel, location, path.stem)
                        indexed += 1
                    except sqlite3.IntegrityError:
                        skipped.append({"path": rel,
                                        "reason": "slug-collision"})
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            con.execute(
                "INSERT OR REPLACE INTO meta VALUES ('last_reindex', ?)",
                (now,))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('skipped', ?)",
                        (json.dumps(skipped),))
            con.commit()
        finally:
            con.close()
    return {"indexed": indexed, "skipped": skipped,
            "duration_ms": int((time.monotonic() - start) * 1000)}


def _insert(con: sqlite3.Connection, meta: dict, body: str, rel: str,
            location: str, slug: str) -> None:
    title = str(meta.get("title", slug))
    tags = meta.get("tags", [])
    tags_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    cur = con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, project, content_hash, created, updated)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(meta["id"]), slug, rel, location,
         str(meta.get("type", "note")), str(meta.get("env", "generic")),
         title, str(meta.get("description", "")),
         meta.get("project"), str(meta.get("content_hash", "")),
         str(meta.get("created", "")), str(meta.get("updated",
                                                    meta.get("created", "")))))
    con.execute(
        "INSERT INTO entries_fts(rowid, title, tags, body, title_norm,"
        " tags_norm, body_norm) VALUES (?,?,?,?,?,?,?)",
        (cur.lastrowid, title, tags_text, body,
         normalize(title), normalize(tags_text), normalize(body)))
