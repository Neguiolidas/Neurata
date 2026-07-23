"""neurata/tick.py — inbox → library, curadoria mecânica sem LLM.

Determinístico e idempotente: cataloga o que dá pra catalogar
mecanicamente, quarentena duplicata exata, marca near-dup como
conflito visível, repara renames/órfãos, registra tudo em journal
append-only. Ver docs/superpowers/specs/2026-07-18-neurata-v0.4-tick.md.

Três camadas de verdade: arquivos = estado, journal = história, index
= cache descartável (drop_schema+reindex sempre reconstrói igual).
Tick só ACRESCENTA chaves de frontmatter — nunca edita valor
existente, nunca toca corpo (exceção aditiva documentada: `conflicts_with`).
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from neurata import indexdb
from neurata.dedup import NEAR_DUP_JACCARD, jaccard, shingle_hashes
from neurata.frontmatter import FrontmatterError, parse, serialize
from neurata.home import NeurataHome
from neurata.indexdb import IndexLock, connect
from neurata.snapshot import commit_tick, ensure_repo
from neurata.textnorm import normalize, slugify
from neurata.ulid import new_ulid


class TickStructuralError(RuntimeError):
    """Falha estrutural do RUN — nada foi tocado. Exit 1 na CLI."""


@dataclass
class ItemError:
    path: str
    reason: str


@dataclass
class TickReport:
    tick: str
    processed: int = 0   # catalogados (inclui alfabetizados e órfãos adotados)
    literate: int = 0    # alfabetizados (subconjunto de processed)
    quarantined: int = 0
    conflicts: int = 0   # catalogados com marca near-dup (subconjunto)
    renamed: int = 0     # reparos do §3
    updated: int = 0     # skill-item source-keyed: update in-place (§5)
    stale: int = 0       # tombstone marcou entry como stale (§5)
    errors: "list[ItemError]" = field(default_factory=list)
    duration_ms: int = 0
    snapshot: "str | None" = None  # sha do commit deste tick, ou None (v0.6)


def curate_tick(home: NeurataHome, budget: "int | None" = None) -> TickReport:
    start = time.monotonic()
    tick_id = new_ulid()
    report = TickReport(tick=tick_id)

    with IndexLock(home):
        con = connect(home)
        try:
            try:
                indexdb.check_schema(con, require_reindexed=False)
            except indexdb.IndexSchemaError as exc:
                raise TickStructuralError(str(exc)) from exc

            if os.stat(home.inbox).st_dev != os.stat(home.library).st_dev:
                raise TickStructuralError(
                    "inbox e library precisam estar no mesmo filesystem — "
                    "rename() atômico exige mesmo dispositivo")

            _reconcile_renames(home, con, tick_id, report)

            items = sorted(home.inbox.glob("*.md"))
            if budget:
                items = items[:budget]

            shingle_sets = dict(indexdb.load_shingle_sets(con))
            for path in items:
                _process_item(home, con, tick_id, path, report, shingle_sets)
        finally:
            con.close()

        try:
            if ensure_repo(home):
                report.snapshot = commit_tick(home, report)
        except Exception as exc:
            home.append_log("snapshot", {"tick": tick_id, "error": str(exc)})
            report.snapshot = None

    report.duration_ms = int((time.monotonic() - start) * 1000)
    return report


# ── item pipeline (§1 passos 2–7) ───────────────────────────────────

def _process_item(home: NeurataHome, con, tick_id: str, path: Path,
                  report: TickReport, shingle_sets: dict) -> None:
    rel_src = _relpath(home, path)

    if not _guard_ok(home, path):
        _quarantine(home, tick_id, path, report,
                   reason="guard: caminho resolve fora de inbox/ "
                          "(symlink ou '..') — mova/revise manualmente",
                   mark_error=True)
        return

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    literate = False
    try:
        meta, body = parse(text)
    except FrontmatterError:
        meta, body = {}, text

    if not meta:
        meta, body = _alphabetize(body, path)
        literate = True
    else:
        meta = dict(meta)
        meta.setdefault("id", new_ulid())

    if not literate:
        if meta.get("type") == "skill-tombstone":
            _process_tombstone(home, con, tick_id, path, meta, report)
            return
        if meta.get("source_key"):
            _process_skill_item(home, con, tick_id, path, meta, body,
                                report, shingle_sets)
            return

    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

    # dedup exato (§1 passo 4)
    row = con.execute(
        "SELECT path FROM entries WHERE location='library' AND"
        " content_hash=?", (content_hash,)).fetchone()
    if row is not None and (home.root / row[0]).is_file():
        _quarantine(home, tick_id, path, report,
                   reason="dup exato (content_hash já na library)",
                   mark_error=False)
        return
    # hash bate mas arquivo sumiu: não é dup — segue pro catalog normal
    # (o preflight §3 já reconcilia entradas mortas/órfãs antes disto).

    # near-dup (§4)
    conflict_new = False
    target_id = None
    shingles = shingle_hashes(body)
    if shingles:
        cur_set = frozenset(shingles)
        best_score = 0.0
        for eid, sset in shingle_sets.items():
            score = jaccard(cur_set, sset)
            if score < NEAR_DUP_JACCARD:
                continue
            if score > best_score or (score == best_score and
                                      (target_id is None or eid < target_id)):
                best_score = score
                target_id = eid
        if target_id is not None:
            existing = meta.get("conflicts_with", [])
            if not isinstance(existing, list):
                existing = [existing] if existing else []
            if target_id not in existing:
                meta["conflicts_with"] = existing + [target_id]
                conflict_new = True
            else:
                meta["conflicts_with"] = existing

    # slug + move (§1 passos 6–7)
    title = str(meta.get("title") or path.stem)
    slug = _unique_slug(con, slugify(title))
    dest = home.library / f"{slug}.md"
    now = _now()
    meta.setdefault("cataloged", now)
    meta.setdefault("content_hash", content_hash)

    new_text = serialize(meta, body)
    try:
        path.write_text(new_text, encoding="utf-8")
        path.rename(dest)
    except OSError as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    rel_dst = _relpath(home, dest)
    _index_insert(con, meta, body, rel_dst, "library", slug)
    con.commit()
    shingle_sets[str(meta["id"])] = frozenset(shingles)

    ok = _journal(home, tick_id, "catalog", str(meta["id"]), rel_src,
                 rel_dst, report, content_hash=content_hash)
    if not ok:
        return
    literate_ok = True
    if literate:
        literate_ok = _journal(home, tick_id, "literate", str(meta["id"]),
                              rel_src, rel_dst, report)
    conflict_ok = True
    if conflict_new:
        conflict_ok = _journal(home, tick_id, "conflict", str(meta["id"]),
                              rel_src, rel_dst, report,
                              conflicts_with=target_id)

    report.processed += 1
    if literate and literate_ok:
        report.literate += 1
    if conflict_new and conflict_ok:
        report.conflicts += 1


# ── §5 source-keyed: skill-item / tombstone ─────────────────────────

def _process_skill_item(home: NeurataHome, con, tick_id: str, path: Path,
                        meta: dict, body: str, report: TickReport,
                        shingle_sets: dict) -> None:
    rel_src = _relpath(home, path)
    source_key = str(meta.get("source_key"))
    row = con.execute(
        "SELECT id, slug, path, content_hash FROM entries"
        " WHERE source_key=? AND location='library'", (source_key,)
    ).fetchone()

    if row is None:
        _catalog_skill_item(home, con, tick_id, path, meta, body, report,
                            shingle_sets, rel_src)
        return

    entry_id, slug, lib_rel, lib_hash = row
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if content_hash == lib_hash:
        # no-op: já refletido na library — só consome o item pendente.
        try:
            path.unlink()
        except OSError as exc:
            report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    _sync_update_in_place(
        home, con, tick_id, entry_id=entry_id, slug=slug,
        lib_rel_path=lib_rel, source_key=source_key, new_meta=meta,
        new_body=body, inbox_path=path, rel_src=rel_src, report=report,
        shingle_sets=shingle_sets)


def _catalog_skill_item(home: NeurataHome, con, tick_id: str, path: Path,
                        meta: dict, body: str, report: TickReport,
                        shingle_sets: dict, rel_src: str) -> None:
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    title = str(meta.get("title") or path.stem)
    slug = _unique_slug(con, slugify(title))
    dest = home.library / f"{slug}.md"
    meta.setdefault("content_hash", content_hash)

    new_text = serialize(meta, body)
    try:
        path.write_text(new_text, encoding="utf-8")
        path.rename(dest)
    except OSError as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    rel_dst = _relpath(home, dest)
    _index_insert(con, meta, body, rel_dst, "library", slug)
    con.commit()
    shingle_sets[str(meta["id"])] = frozenset(shingle_hashes(body))

    ok = _journal(home, tick_id, "catalog", str(meta["id"]), rel_src,
                 rel_dst, report, content_hash=content_hash)
    if not ok:
        return
    report.processed += 1


def _sync_update_in_place(home: NeurataHome, con, tick_id: str, *,
                          entry_id: str, slug: str, lib_rel_path: str,
                          source_key: "str | None", new_meta: dict,
                          new_body: str, inbox_path: Path, rel_src: str,
                          report: TickReport, shingle_sets: dict) -> None:
    """Update-in-place de entry source-keyed. Nunca reconstrói o meta do
    zero: parte do meta antigo (lido da library) e sobrescreve só o
    subconjunto §4 + content_hash/updated, limpando stale/stale_since se
    presentes. Preserva id/slug/path/created e toda chave extra."""
    assert source_key is not None

    lib_path = home.root / lib_rel_path
    old_meta, _old_body = parse(lib_path.read_text(encoding="utf-8"))
    merged = dict(old_meta)
    for key in ("title", "description", "env", "source_key", "source_path"):
        if key in new_meta:
            merged[key] = new_meta[key]
    merged.pop("stale", None)
    merged.pop("stale_since", None)
    content_hash = hashlib.sha256(new_body.encode("utf-8")).hexdigest()
    merged["content_hash"] = content_hash
    merged["updated"] = _now()

    new_text = serialize(merged, new_body)
    try:
        lib_path.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    _index_delete(con, entry_id)
    _index_insert(con, merged, new_body, lib_rel_path, "library", slug)
    con.commit()
    item_id = str(merged.get("id", entry_id))
    shingle_sets[item_id] = frozenset(shingle_hashes(new_body))

    try:
        inbox_path.unlink()
    except OSError as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    ok = _journal(home, tick_id, "update", item_id, rel_src, lib_rel_path,
                 report, content_hash=content_hash)
    if not ok:
        return
    report.updated += 1


def _index_delete(con, entry_id: str) -> None:
    row = con.execute(
        "SELECT rowid FROM entries WHERE id=?", (entry_id,)).fetchone()
    if row is None:
        return
    rowid = row[0]
    con.execute("DELETE FROM entries_fts WHERE rowid=?", (rowid,))
    con.execute("DELETE FROM entry_tags WHERE entry_rowid=?", (rowid,))
    con.execute("DELETE FROM entries WHERE id=?", (entry_id,))


def _process_tombstone(home: NeurataHome, con, tick_id: str, path: Path,
                       meta: dict, report: TickReport) -> None:
    rel_src = _relpath(home, path)
    source_key = meta.get("source_key")
    row = None
    if source_key:
        row = con.execute(
            "SELECT path FROM entries WHERE source_key=? AND"
            " location='library'", (str(source_key),)).fetchone()

    if row is None:
        try:
            path.unlink()
        except OSError as exc:
            report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
            return
        _journal(home, tick_id, "noop", None, rel_src, None, report,
                reason="tombstone: source_key nao encontrado na library")
        return

    lib_rel = row[0]
    lib_path = home.root / lib_rel
    try:
        old_meta, body = parse(lib_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, FrontmatterError) as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    new_meta = dict(old_meta)
    new_meta["stale"] = "true"
    new_meta["stale_since"] = _now()
    new_text = serialize(new_meta, body)
    try:
        lib_path.write_text(new_text, encoding="utf-8")
        path.unlink()
    except OSError as exc:
        report.errors.append(ItemError(rel_src, f"transiente (I/O): {exc}"))
        return

    ok = _journal(home, tick_id, "stale", str(new_meta.get("id")), rel_src,
                 lib_rel, report)
    if not ok:
        return
    report.stale += 1


def _guard_ok(home: NeurataHome, path: Path) -> bool:
    real = path.resolve(strict=False)
    inbox_real = home.inbox.resolve(strict=False)
    return real.parent == inbox_real


def _alphabetize(body: str, path: Path) -> "tuple[dict, str]":
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    title = _extract_title(body) or path.stem
    meta = {
        "id": new_ulid(),
        "title": title,
        "type": "note",
        "created": _now(),
        "grain_quality": "mechanical",
        "content_hash": content_hash,
    }
    return meta, body


def _extract_title(body: str) -> "str | None":
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            t = s.lstrip("#").strip()
            if t:
                return t
    return None


def _unique_slug(con, base: str) -> str:
    slug = base
    n = 2
    while con.execute(
            "SELECT 1 FROM entries WHERE slug=?", (slug,)).fetchone():
        slug = f"{base}-{n}"
        n += 1
    return slug


def _quarantine(home: NeurataHome, tick_id: str, path: Path,
                report: TickReport, *, reason: str, mark_error: bool,
                item_id: "str | None" = None) -> None:
    rel_src = _relpath(home, path)
    dest = home.quarantine / path.name
    n = 2
    while dest.exists() or dest.is_symlink():
        dest = home.quarantine / f"{path.stem}-{n}{path.suffix}"
        n += 1
    path.rename(dest)
    rel_dst = _relpath(home, dest)
    ok = _journal(home, tick_id, "quarantine", item_id, rel_src, rel_dst,
                 report, reason=reason)
    if ok:
        report.quarantined += 1
    if mark_error:
        report.errors.append(ItemError(rel_src, reason))


def _is_safe_journal_path(path: "str | None") -> bool:
    """True se path é relativo e sem componente `..` (previne escrita
    fora da árvore do repo via journal com path malicioso/corrompido)."""
    if path is None:
        return True
    pure = PurePosixPath(path)
    if pure.is_absolute():
        return False
    if ".." in pure.parts:
        return False
    return True


def _journal(home: NeurataHome, tick_id: str, verb: str,
            item: "str | None", src: "str | None", dst: "str | None",
            report: TickReport, **extra: object) -> bool:
    if not _is_safe_journal_path(src) or not _is_safe_journal_path(dst):
        report.errors.append(ItemError(
            dst or src or "",
            f"path suspeito (absoluto ou com '..') rejeitado no journal:"
            f" src={src!r} dst={dst!r}"))
        return False
    rec = {"ts": _now(), "tick": tick_id, "verb": verb, "item": item,
          "src": src, "dst": dst}
    rec.update(extra)
    try:
        home.append_log("journal", rec)
        return True
    except OSError as exc:
        report.errors.append(ItemError(
            dst or src or "", f"falha ao gravar journal: {exc}"))
        return False


def _relpath(home: NeurataHome, path: Path) -> str:
    return str(path.relative_to(home.root))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── §3 preflight: renames + reconciliação de órfãos ─────────────────

def _reconcile_renames(home: NeurataHome, con, tick_id: str,
                       report: TickReport) -> None:
    rows = con.execute(
        "SELECT rowid, id, path, content_hash FROM entries"
        " WHERE location='library'").fetchall()
    index_paths = {path for _, _, path, _ in rows}
    missing = [(rowid, eid, path, chash) for rowid, eid, path, chash in rows
              if not (home.root / path).is_file()]

    disk_files: dict = {}
    for p in sorted(home.library.rglob("*.md")):
        rel = str(p.relative_to(home.root))
        if rel in index_paths:
            continue
        try:
            text = p.read_text(encoding="utf-8")
            meta, body = parse(text)
        except (OSError, UnicodeDecodeError, FrontmatterError):
            continue
        chash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        disk_files[rel] = (meta, body, chash)

    missing_by_hash: dict = {}
    for rowid, eid, path, chash in missing:
        missing_by_hash.setdefault(chash, []).append((rowid, eid, path))
    disk_by_hash: dict = {}
    for rel, (_meta, _body, chash) in disk_files.items():
        disk_by_hash.setdefault(chash, []).append(rel)

    handled: set = set()
    consumed: set = set()
    for chash, candidates_entries in missing_by_hash.items():
        candidates_entries.sort(key=lambda t: t[1])  # menor ULID ganha
        candidates_files = sorted(disk_by_hash.get(chash, []))
        for (rowid, eid, old_path), new_path in zip(candidates_entries,
                                                     candidates_files):
            con.execute("UPDATE entries SET path=? WHERE id=?",
                       (new_path, eid))
            con.commit()
            ok = _journal(home, tick_id, "rename", eid, old_path, new_path,
                         report)
            handled.add(eid)
            if not ok:
                continue
            consumed.add(new_path)
            report.renamed += 1

    # entrada morta: sem arquivo e sem par por hash → remove (índice é
    # cache; estado > história).
    for rowid, eid, old_path, chash in missing:
        if eid in handled:
            continue
        con.execute("DELETE FROM entries_fts WHERE rowid=?", (rowid,))
        con.execute("DELETE FROM entry_tags WHERE entry_rowid=?", (rowid,))
        con.execute("DELETE FROM entries WHERE id=?", (eid,))
        con.commit()
        reason = ("entrada morta no índice removida — arquivo ausente e "
                  "sem par por content_hash; busque em quarantine/journal")
        _journal(home, tick_id, "error", eid, old_path, None, report,
                reason=reason)
        report.errors.append(ItemError(old_path, reason))

    # órfão: arquivo em library/ fora do índice, sem par → adota.
    for rel, (meta, body, chash) in disk_files.items():
        if rel in consumed:
            continue
        eid = meta.get("id")
        if not eid:
            continue  # sem id não há como indexar; reindex full reporta.
        eid = str(eid)
        exists = con.execute(
            "SELECT 1 FROM entries WHERE id=? OR path=?",
            (eid, rel)).fetchone()
        if exists:
            continue
        slug = Path(rel).stem
        if con.execute(
                "SELECT 1 FROM entries WHERE slug=?", (slug,)).fetchone():
            continue  # colisão de slug num órfão: deixa pro reindex full.
        _index_insert(con, meta, body, rel, "library", slug)
        con.commit()
        ok = _journal(home, tick_id, "catalog", eid, None, rel, report,
                     content_hash=chash)
        if not ok:
            continue
        report.processed += 1


# ── índice incremental (subset de reindex._insert, sem grains/links) ─

def _index_insert(con, meta: dict, body: str, rel: str, location: str,
                  slug: str) -> int:
    title = str(meta.get("title", slug))
    tags = meta.get("tags", [])
    tag_list = [str(t) for t in tags] if isinstance(tags, list) else [str(tags)]
    tag_list = [t for t in tag_list if t.strip()]
    tags_text = " ".join(tag_list)
    aliases_text = " ".join(_aliases(meta))
    grain_quality = str(meta.get("grain_quality", "mechanical")) or "mechanical"
    shingles_json = _dumps(shingle_hashes(body))
    source_key = meta.get("source_key")
    description = str(meta.get("description", ""))
    fts_body = f"{description}\n\n{body}" if description else body
    cur = con.execute(
        "INSERT INTO entries(id, slug, path, location, type, env, title,"
        " description, project, content_hash, created, updated,"
        " grain_quality, shingles, source_key)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(meta["id"]), slug, rel, location,
         str(meta.get("type", "note")), str(meta.get("env", "generic")),
         title, description,
         meta.get("project"), str(meta.get("content_hash", "")),
         str(meta.get("created", "")),
         str(meta.get("updated", meta.get("created", ""))),
         grain_quality, shingles_json,
         str(source_key) if source_key else None))
    rowid = cur.lastrowid
    assert rowid is not None
    con.execute(
        "INSERT INTO entries_fts(rowid, title, aliases, tags, body,"
        " title_norm, aliases_norm, tags_norm, body_norm)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (rowid, title, aliases_text, tags_text, fts_body, normalize(title),
         normalize(aliases_text), normalize(tags_text), normalize(fts_body)))
    for tag in {t.lower() for t in tag_list}:
        con.execute("INSERT OR IGNORE INTO entry_tags VALUES (?,?)",
                   (rowid, tag))
    return rowid


def _aliases(meta: dict) -> "list[str]":
    v = meta.get("aliases", [])
    if isinstance(v, list):
        return [str(a) for a in v if str(a).strip()]
    return [str(v)] if str(v).strip() else []


def _dumps(value: object) -> str:
    return json.dumps(value)
