"""neurata/indexdb.py — índice sqlite descartável. FTS5 = requisito duro."""
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

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
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
  updated TEXT NOT NULL
);
"""

_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5("
        "title, tags, body, title_norm, tags_norm, body_norm, "
        "prefix='2 3 4')")


class FTS5MissingError(RuntimeError):
    def __init__(self) -> None:
        super().__init__(_REMEDY)


class LockHeldError(RuntimeError):
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


def drop_schema(con: sqlite3.Connection) -> None:
    con.execute("DROP TABLE IF EXISTS entries_fts")
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
