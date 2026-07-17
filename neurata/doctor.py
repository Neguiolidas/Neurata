"""neurata/doctor.py — self-check com remediação. Nunca degrada em silêncio."""
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime

from neurata import config as query_config
from neurata.home import SCHEMA_VERSION, NeurataHome
from neurata.indexdb import INDEX_SCHEMA_VERSION, fts5_available


@dataclass
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str
    remedy: str = field(default="")


def run_checks(home: NeurataHome) -> list[Check]:
    checks = [_python(), _layout(home)]
    if checks[-1].status == "fail":
        return checks
    checks.append(_config(home))
    checks.append(_query_config(home))
    checks.append(_fts5(home))
    checks.append(_index(home))
    checks.append(_index_schema(home))
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


def _layout(home: NeurataHome) -> Check:
    missing = [d.name for d in (home.library, home.inbox, home.archive,
                                home.quarantine, home.logs) if not d.is_dir()]
    if missing:
        return Check("home-layout", "fail", f"faltando: {missing}",
                     "rode `neurata doctor` após `neurata deposit` inicial "
                     "ou crie o home: qualquer comando faz init")
    return Check("home-layout", "ok", str(home.root))


def _config(home: NeurataHome) -> Check:
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


def _query_config(home: NeurataHome) -> Check:
    try:
        query_config.load(home)
    except query_config.ConfigError as exc:
        return Check("query-config", "fail", str(exc),
                     "corrija config.json (chaves/valores de query)")
    return Check("query-config", "ok", "válida")


def _index_schema(home: NeurataHome) -> Check:
    if not home.index_path.exists():
        return Check("index-schema", "ok", "index.db ausente (ver check "
                     "index)")
    v = _meta(home, "index_schema_version")
    if v is None or str(v) != str(INDEX_SCHEMA_VERSION):
        return Check("index-schema", "warn",
                     f"index_schema_version={v} "
                     f"(esperado {INDEX_SCHEMA_VERSION})",
                     "rode `neurata reindex`")
    return Check("index-schema", "ok",
                 f"index_schema_version={INDEX_SCHEMA_VERSION}")


def _fts5(home: NeurataHome) -> Check:
    con = sqlite3.connect(":memory:")
    try:
        ok = fts5_available(con)
    finally:
        con.close()
    return Check("fts5", "ok" if ok else "fail",
                 "disponível" if ok else "INDISPONÍVEL",
                 "" if ok else "instale Python/sqlite com FTS5 habilitado")


def _index(home: NeurataHome) -> Check:
    if not home.index_path.exists():
        return Check("index", "warn", "index.db ausente",
                     "rode `neurata reindex`")
    con = sqlite3.connect(home.index_path)
    try:
        con.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
    except sqlite3.DatabaseError:
        return Check("index", "fail", "index.db corrompido (não é sqlite)",
                     "apague index.db e rode `neurata reindex` "
                     "(o índice é cache descartável)")
    finally:
        con.close()
    return Check("index", "ok", str(home.index_path))


def _meta(home: NeurataHome, key: str) -> "str | None":
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


def _freshness(home: NeurataHome) -> Check:
    last = _meta(home, "last_reindex")
    if last is None:
        return Check("index-freshness", "warn", "sem last_reindex",
                     "rode `neurata reindex`")
    last_ts = datetime.fromisoformat(last).timestamp()
    # last_reindex tem resolução de segundos — floor dos dois lados
    stale = [str(p.relative_to(home.root))
             for base in (home.library, home.inbox)
             for p in base.rglob("*.md")
             if int(p.stat().st_mtime) > int(last_ts)]
    if stale:
        return Check("index-freshness", "warn",
                     f"{len(stale)} arquivo(s) mais novos que o índice",
                     "rode `neurata reindex`")
    return Check("index-freshness", "ok", f"last_reindex={last}")


def _skipped(home: NeurataHome) -> Check:
    raw = _meta(home, "skipped")
    skipped = json.loads(raw) if raw else []
    if skipped:
        paths = ", ".join(s["path"] for s in skipped)
        return Check("skipped-files", "warn", paths,
                     "corrija o frontmatter dos arquivos pulados")
    return Check("skipped-files", "ok", "nenhum")


def _lock(home: NeurataHome) -> Check:
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
                     "remova index.lock ou rode `neurata reindex` "
                     "(toma posse de lock stale)")
