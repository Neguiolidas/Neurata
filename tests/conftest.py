"""tests/conftest.py — forjas de schema legado compartilhadas.

Migração e carimbo só podem ser testados contra um índice realmente
antigo. Recriar a DDL da v7 aqui é a forma honesta de produzir esse
estado: um índice v8 com a linha de `meta` adulterada mentiria sobre as
colunas e deixaria passar exatamente os bugs que estes testes caçam.
"""
import sqlite3

# `entries` na v7 (pré-procedência): a DDL da v8 menos agent/session/origin.
_V7_ENTRIES = """
CREATE TABLE entries(
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
  source_key TEXT,
  regime TEXT NOT NULL DEFAULT 'curated'
         CHECK(regime IN ('mirror', 'curated'))
);
"""


def forge_v7_entries(con: sqlite3.Connection) -> None:
    """Substitui `entries` pela DDL da v7 (destrutivo: a tabela some)."""
    con.executescript("DROP TABLE IF EXISTS entries;" + _V7_ENTRIES)
    con.commit()


def set_index_version(con: sqlite3.Connection, version: "int | None") -> None:
    """Grava (ou remove, com `None`) o carimbo de versão do índice."""
    if version is None:
        con.execute("DELETE FROM meta WHERE key='index_schema_version'")
    else:
        con.execute("INSERT OR REPLACE INTO meta VALUES"
                    " ('index_schema_version', ?)", (str(version),))
    con.commit()


def insert_entry(con: sqlite3.Connection, entry_id: str, slug: str,
                 path: str, title: str, **extra: object) -> None:
    """INSERT mínimo em `entries`, com as colunas comuns a v7 e v8."""
    cols = {"id": entry_id, "slug": slug, "path": path, "location": "library",
            "title": title, "content_hash": "h" * 64, "created": "2026-01-01",
            "updated": "2026-01-01", "shingles": "[]", **extra}
    names = ", ".join(cols)
    marks = ", ".join("?" * len(cols))
    con.execute(f"INSERT INTO entries({names}) VALUES ({marks})",
                tuple(cols.values()))
    con.commit()
