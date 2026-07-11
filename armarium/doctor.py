"""armarium/doctor.py — self-check com remediação. Nunca degrada em silêncio."""
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime

from armarium.home import SCHEMA_VERSION, ArmariumHome
from armarium.indexdb import fts5_available


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    remedy: str = field(default="")


def run_checks(home: ArmariumHome) -> list[Check]:
    checks = [_python(), _layout(home)]
    if checks[-1].status == "fail":
        return checks
    checks.append(_config(home))
    checks.append(_fts5(home))
    checks.append(_index(home))
    checks.append(_freshness(home))
    checks.append(_skipped(home))
    checks.append(_lock(home))
    return checks


def exit_code(checks: list[Check]) -> int:
    if any(c.status == "fail" for c in checks):
        return 2
    if any(c.status == "warn" for c in checks):
        return 1
    return 0


def _python() -> Check:
    ok = sys.version_info >= (3, 10)
    return Check("python-version", "ok" if ok else "fail",
                 f"{sys.version_info.major}.{sys.version_info.minor}",
                 "" if ok else "instale Python >= 3.10")


def _layout(home: ArmariumHome) -> Check:
    missing = [d.name for d in (home.library, home.inbox, home.archive,
                                home.quarantine, home.logs) if not d.is_dir()]
    if missing:
        return Check("home-layout", "fail", f"faltando: {missing}",
                     "rode `armarium doctor` após `armarium deposit` inicial "
                     "ou crie o home: qualquer comando faz init")
    return Check("home-layout", "ok", str(home.root))


def _config(home: ArmariumHome) -> Check:
    try:
        cfg = home.load_config()
    except (OSError, ValueError):
        return Check("config", "fail", "config.json ilegível",
                     "restaure/apague config.json e rode init de novo")
    if cfg.get("schema_version") != SCHEMA_VERSION:
        return Check("config", "fail",
                     f"schema_version={cfg.get('schema_version')} "
                     f"(esperado {SCHEMA_VERSION})",
                     "migração de schema — ver spec §16.5")
    return Check("config", "ok", f"schema_version={SCHEMA_VERSION}")


def _fts5(home: ArmariumHome) -> Check:
    con = sqlite3.connect(":memory:")
    try:
        ok = fts5_available(con)
    finally:
        con.close()
    return Check("fts5", "ok" if ok else "fail",
                 "disponível" if ok else "INDISPONÍVEL",
                 "" if ok else "instale Python/sqlite com FTS5 habilitado")


def _index(home: ArmariumHome) -> Check:
    if not home.index_path.exists():
        return Check("index", "warn", "index.db ausente",
                     "rode `armarium reindex`")
    con = sqlite3.connect(home.index_path)
    try:
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.DatabaseError:
        return Check("index", "fail", "index.db corrompido (não é sqlite)",
                     "apague index.db e rode `armarium reindex` "
                     "(o índice é cache descartável)")
    finally:
        con.close()
    return Check("index", "ok", str(home.index_path))


def _meta(home: ArmariumHome, key: str) -> "str | None":
    if not home.index_path.exists():
        return None
    con = sqlite3.connect(home.index_path)
    try:
        row = con.execute("SELECT value FROM meta WHERE key=?",
                          (key,)).fetchone()
        return row[0] if row else None
    except sqlite3.DatabaseError:  # cobre OperationalError e corrupção
        return None
    finally:
        con.close()


def _freshness(home: ArmariumHome) -> Check:
    last = _meta(home, "last_reindex")
    if last is None:
        return Check("index-freshness", "warn", "sem last_reindex",
                     "rode `armarium reindex`")
    last_ts = datetime.fromisoformat(last).timestamp()
    # last_reindex tem resolução de segundos — floor dos dois lados
    stale = [str(p.relative_to(home.root))
             for base in (home.library, home.inbox)
             for p in base.rglob("*.md")
             if int(p.stat().st_mtime) > int(last_ts)]
    if stale:
        return Check("index-freshness", "warn",
                     f"{len(stale)} arquivo(s) mais novos que o índice",
                     "rode `armarium reindex`")
    return Check("index-freshness", "ok", f"last_reindex={last}")


def _skipped(home: ArmariumHome) -> Check:
    raw = _meta(home, "skipped")
    skipped = json.loads(raw) if raw else []
    if skipped:
        paths = ", ".join(s["path"] for s in skipped)
        return Check("skipped-files", "warn", paths,
                     "corrija o frontmatter dos arquivos pulados")
    return Check("skipped-files", "ok", "nenhum")


def _lock(home: ArmariumHome) -> Check:
    lock = home.root / "index.lock"
    if not lock.exists():
        return Check("lock", "ok", "livre")
    try:
        pid = int(lock.read_text().strip())
        import os
        os.kill(pid, 0)
        return Check("lock", "warn", f"lock ativo (pid {pid})",
                     "reindex em andamento? aguarde ou investigue")
    except (ValueError, OSError):
        return Check("lock", "warn", "lock STALE (processo morto)",
                     "remova index.lock ou rode `armarium reindex` "
                     "(toma posse de lock stale)")
