"""neurata/indexdb.py — índice sqlite descartável. FTS5 = requisito duro."""
import json
import os
import sqlite3

from neurata.home import NeurataHome

_REMEDY = (
    "FTS5 indisponível no sqlite deste Python. O Neurata exige FTS5 "
    "(flag de compilação do sqlite do sistema). Remediação: instale um "
    "Python/sqlite com FTS5 (Ubuntu/Debian/Homebrew padrão têm; "
    "`python3 -c \"import sqlite3; print(sqlite3.sqlite_version)\"` e "
    "verifique a build)."
)

# Versão do schema do ÍNDICE (meta 'index_schema_version'); distinta do
# SCHEMA_VERSION do config em home.py. Só o reindex grava; check_schema
# (público) checa — v6: coluna `source_key` (Phase 1/v0.5 harvest).
INDEX_SCHEMA_VERSION = 6

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS entry_tags(
  entry_rowid INTEGER NOT NULL,
  tag TEXT NOT NULL,
  PRIMARY KEY(entry_rowid, tag)
);
CREATE INDEX IF NOT EXISTS idx_entry_tags_tag ON entry_tags(tag);
CREATE TABLE IF NOT EXISTS edges(
  src_rowid INTEGER NOT NULL,
  dst_rowid INTEGER NOT NULL,
  PRIMARY KEY(src_rowid, dst_rowid)
);
CREATE TABLE IF NOT EXISTS grains(
  entry_id TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('card','summary')),
  text TEXT NOT NULL,
  src_hash TEXT NOT NULL,
  PRIMARY KEY(entry_id, kind)
);
CREATE TABLE IF NOT EXISTS entries(
  rowid INTEGER PRIMARY KEY,
  id TEXT NOT NULL UNIQUE,
  slug TEXT NOT NULL UNIQUE,
  path TEXT NOT NULL,
  location TEXT NOT NULL CHECK(location IN ('library','inbox')),
  type TEXT NOT NULL DEFAULT 'note',
  env TEXT NOT NULL DEFAULT 'generic',
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  project TEXT,
  content_hash TEXT NOT NULL,
  created TEXT NOT NULL,
  updated TEXT NOT NULL,
  grain_quality TEXT NOT NULL DEFAULT 'mechanical',
  shingles TEXT NOT NULL,
  source_key TEXT
);
"""

_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5("
        "title, aliases, tags, body, "
        "title_norm, aliases_norm, tags_norm, body_norm, "
        "prefix='2 3 4')")


class FTS5MissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(_REMEDY)


class LockHeldError(RuntimeError):
    pass


class IndexSchemaError(RuntimeError):
    pass


def connect(home: NeurataHome) -> sqlite3.Connection:
    con = sqlite3.connect(home.index_path)
    con.execute("PRAGMA journal_mode=WAL")
    ensure_fts5(con)
    create_schema(con)
    return con


def fts5_available(con: sqlite3.Connection) -> bool:
    try:
        con.execute("CREATE VIRTUAL TABLE temp.fts5_probe USING fts5(x)")
        con.execute("DROP TABLE temp.fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def ensure_fts5(con: sqlite3.Connection) -> None:
    if not fts5_available(con):
        raise FTS5MissingError()


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.execute(_FTS)
    con.commit()


def check_schema(con: sqlite3.Connection,
                 require_reindexed: bool = True) -> None:
    """Levanta se o índice está em versão != atual (schema corrompido/
    antigo) — sempre, independente de `require_reindexed`. Se ainda não
    houver linha de versão (índice nunca reindexado), só levanta quando
    `require_reindexed=True` (comportamento de `query`, que exige dados
    já indexados pra buscar). `tick` passa `require_reindexed=False`:
    um NEURATA_HOME recém-inicializado, nunca reindexado, não é uma
    falha estrutural pra curadoria mecânica — é só ausência de dados
    (shingle_sets vazio), tratado normalmente pelo dedup.

    SELECT puro no meta — zero efeito colateral. Público (promovido de
    `query._check_schema`) pra `tick` também poder validar na entrada
    do run, antes de rodar near-dup contra um índice sem coluna
    `shingles` (v4).
    """
    row = con.execute(
        "SELECT value FROM meta WHERE key='index_schema_version'").fetchone()
    if row is None:
        if require_reindexed:
            raise IndexSchemaError(
                "índice ausente ou em schema antigo — rode "
                "`neurata reindex`")
        return
    if str(row[0]) != str(INDEX_SCHEMA_VERSION):
        raise IndexSchemaError(
            "índice ausente ou em schema antigo — rode `neurata reindex`")


def load_shingle_sets(con: sqlite3.Connection) -> "dict[str, frozenset]":
    """{entry.id: frozenset(shingle-hashes)} pra near-dup em Task 4."""
    rows = con.execute("SELECT id, shingles FROM entries").fetchall()
    return {eid: frozenset(json.loads(shingles)) for eid, shingles in rows}


def drop_schema(con: sqlite3.Connection) -> None:
    con.execute("DROP TABLE IF EXISTS entries_fts")
    con.execute("DROP TABLE IF EXISTS grains")
    con.execute("DROP TABLE IF EXISTS edges")
    con.execute("DROP TABLE IF EXISTS entry_tags")
    con.execute("DROP TABLE IF EXISTS entries")
    con.execute("DROP TABLE IF EXISTS meta")
    con.commit()


class IndexLock:
    def __init__(self, home: NeurataHome):
        self.path = home.root / "index.lock"

    def __enter__(self) -> "IndexLock":
        try:
            self._acquire()
        except FileExistsError:
            if self._is_stale():
                self.path.unlink(missing_ok=True)
                self._acquire()
            else:
                raise LockHeldError(f"lock ativo: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.path.unlink(missing_ok=True)

    def _acquire(self) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)

    def _is_stale(self) -> bool:
        try:
            pid = int(self.path.read_text().strip())
            os.kill(pid, 0)
            return False
        except (ValueError, OSError, ProcessLookupError):
            return True
