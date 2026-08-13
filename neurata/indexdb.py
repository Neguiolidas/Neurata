"""neurata/indexdb.py — índice sqlite descartável. FTS5 = requisito duro."""
import functools
import json
import os
import sqlite3
from pathlib import PurePosixPath

from neurata import frontmatter
from neurata.frontmatter import FrontmatterError
from neurata.home import NeurataHome
from neurata.textnorm import normalize

_REMEDY = (
    "FTS5 indisponível no sqlite deste Python. O Neurata exige FTS5 "
    "(flag de compilação do sqlite do sistema). Remediação: instale um "
    "Python/sqlite com FTS5 (Ubuntu/Debian/Homebrew padrão têm; "
    "`python3 -c \"import sqlite3; print(sqlite3.sqlite_version)\"` e "
    "verifique a build)."
)

# Versão do schema do ÍNDICE (meta 'index_schema_version'); distinta do
# SCHEMA_VERSION do config em home.py. Reindex e migração gravam;
# check_schema (público) checa — v8: colunas de procedência
# (`agent`/`session`/`origin`) sobre a v7 (coluna `regime` + `curated_fts`).
INDEX_SCHEMA_VERSION = 8

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
  source_key TEXT,
  regime TEXT NOT NULL DEFAULT 'curated'
         CHECK(regime IN ('mirror', 'curated')),
  agent TEXT,
  session TEXT,
  origin TEXT
);
"""

# NULL é o valor certo pras três: procedência é do regime curado, e mesmo
# lá o envelope `source:` é opcional. Sem DEFAULT, portanto — string vazia
# seria um valor inventado que `missing:` teria de desfazer.
#
# `CREATE TABLE IF NOT EXISTS` NÃO altera uma `entries` v7 já em disco:
# índice antigo continua sem estas colunas até a migração rodar. É por isso
# que `_MIGRATIONS` existe, e é por isso que `stamp_if_unversioned` verifica
# as colunas em vez de deduzir a versão.

# Índices sobre `entries` ficam fora de `_SCHEMA`: `CREATE TABLE IF NOT
# EXISTS` não altera uma `entries` legada (v5/v6) já em disco, então criar
# índice sobre coluna nova falharia com OperationalError dentro de
# `connect()` — inclusive no `reindex`, o único comando capaz de consertar.
# Índice sobre tabela legada não tem uso: `reindex` a derruba e recria.
_ENTRIES_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_entries_regime ON entries(regime)",
)

_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5("
        "title, aliases, tags, body, "
        "title_norm, aliases_norm, tags_norm, body_norm, "
        "prefix='2 3 4')")

# Pista do regime curado: mesmo texto, mesmo rowid, corpus pequeno. Existe
# por medição, não por elegância — filtrar `entries_fts` por regime custa
# 100-250 ms num corpus de 15k (o espelho domina a lista de postings do
# termo); a pista responde em 0,3 ms porque o corpus dela são só os grãos
# deliberados. Índice é cache descartável: o custo de duplicar o texto
# curado (1,3 MiB em 106 grãos) é 0,5% do índice.
_CURATED_FTS = ("CREATE VIRTUAL TABLE IF NOT EXISTS curated_fts USING fts5("
                "title, aliases, tags, body, "
                "title_norm, aliases_norm, tags_norm, body_norm, "
                "prefix='2 3 4')")


def fts_insert(con: sqlite3.Connection, rowid: int, regime: str, *,
               title: str, aliases: str, tags: str, body: str) -> None:
    """Indexa o texto do grão: sempre no FTS principal, e na pista se curado.

    Ponto único de escrita de texto no índice. A normalização mora aqui de
    propósito: estava duplicada nos dois writers, que é como dois caminhos
    divergem sem ninguém perceber."""
    cols = (rowid, title, aliases, tags, body,
            normalize(title), normalize(aliases), normalize(tags),
            normalize(body))
    tables = ("entries_fts", "curated_fts") if regime == "curated" \
        else ("entries_fts",)
    for table in tables:
        con.execute(
            f"INSERT INTO {table}(rowid, title, aliases, tags, body,"  # nosec B608
            " title_norm, aliases_norm, tags_norm, body_norm)"
            " VALUES (?,?,?,?,?,?,?,?,?)", cols)


def entry_purge(con: sqlite3.Connection, rowid: int, entry_id: str) -> None:
    """Remove o grão de TODAS as tabelas do índice.

    Ponto único de remoção. `curated_fts` sem a linha é no-op, então não
    precisa checar regime. Sem isto seriam quatro DELETEs copiados em três
    pontos do `tick`, e o esquecido viraria grão fantasma: some da
    biblioteca, continua respondendo na busca."""
    con.execute("DELETE FROM entries_fts WHERE rowid=?", (rowid,))
    con.execute("DELETE FROM curated_fts WHERE rowid=?", (rowid,))
    con.execute("DELETE FROM entry_tags WHERE entry_rowid=?", (rowid,))
    con.execute("DELETE FROM entries WHERE id=?", (entry_id,))


def regime_of(meta: dict) -> str:
    """Regime derivado do dialeto do frontmatter — nunca autorado.

    `source_key` é escrito só pelo `tick` ao espelhar uma fonte externa; sua
    presença é a definição de grão espelhado. Todo o resto (envelope `source:`
    do deposit, grão escrito à mão) é curado. Derivar em vez de ler uma chave
    `regime:` do arquivo elimina o único modo de falha que importa aqui:
    índice e arquivo discordarem.
    """
    return "mirror" if meta.get("source_key") else "curated"


def provenance(meta: dict) -> "tuple[str | None, str | None, str | None]":
    """(agent, session, origin) do envelope `source:` — NULL quando ausente.

    Frontmatter é entrada não confiável: `source:` pode vir escalar, e cada
    campo pode vir com tipo errado. Nada aqui coage — `str(["x"])` indexaria
    `"['x']"` como nome de agente, inventando uma procedência que não existe.
    Vazio e ausente colapsam em NULL para que `missing:agent` signifique uma
    coisa só. Espelho não tem autor — o sistema de origem já está em
    `source_key` — e um `source:` que apareça num arquivo espelhado é do
    autor original, não de quem depositou: regime mirror é NULL por
    construção, não por acaso do conteúdo espelhado.
    """
    src = meta.get("source")
    if meta.get("source_key") or not isinstance(src, dict):
        return (None, None, None)

    def _field(key: str) -> "str | None":
        v = src.get(key)
        return v.strip() or None if isinstance(v, str) else None

    return (_field("agent"), _field("session"), _field("origin"))


def project_of(meta: dict) -> "str | None":
    """Projeto do grão: explícito no frontmatter, senão o basename do
    `source.git.root` do envelope. `None` quando não dá pra saber.

    Derivado em vez de gravado (D3): o arquivo já contém a verdade e o
    índice é descartável — gravar `project:` duplicaria um valor derivável
    e criaria duas fontes que podem discordar. Mirror é `None` por
    construção, como em `provenance()`: cache de sistema externo não é
    trabalho feito num repo meu. Não coage tipo: `project: [a, b]` vira
    `None`, não `"['a', 'b']"` — e antes desta função uma lista aqui
    derrubava o tick inteiro no bind do INSERT.
    """
    if meta.get("source_key"):
        return None

    explicit = meta.get("project")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    src = meta.get("source")
    git = src.get("git") if isinstance(src, dict) else None
    root = git.get("root") if isinstance(git, dict) else None
    if not isinstance(root, str) or not root.strip():
        return None
    return PurePosixPath(root.strip()).name or None


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


def stamp_if_unversioned(con: sqlite3.Connection) -> None:
    """Carimba um índice sem linha de versão quando ele *comprovadamente*
    já está no schema atual, pra destravar `query` sem `reindex` redundante.

    A prova é a tabela: as colunas de `entries` contêm as da versão corrente.
    A versão anterior deduzia isso de "houve INSERT, logo o schema é novo" —
    argumento que só vale num run que inseriu algo. Um tick de `inbox` vazia
    sobre um índice v7 não insere nada e carimbaria v8 sem base, promovendo
    o índice velho a `current` e desligando toda a cura a jusante: o próximo
    INSERT com proveniência morre em `table entries has no column named
    agent`, e `query` deixa de reindexar por achar o índice em dia.

    Índice vazio NÃO é carimbado — indistinguível de haver arquivos em
    library/ nunca indexados; só `reindex()` promove esse caso."""
    has_version = con.execute(
        "SELECT 1 FROM meta WHERE key='index_schema_version'").fetchone()
    if has_version:
        return
    has_entries = con.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
    if not has_entries:
        return
    if not _current_entries_columns() <= entries_columns(con):
        return
    con.execute(
        "INSERT OR REPLACE INTO meta VALUES ('index_schema_version', ?)",
        (str(INDEX_SCHEMA_VERSION),))
    con.commit()


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


def entries_columns(con: sqlite3.Connection) -> set:
    return {row[1] for row in con.execute("PRAGMA table_info(entries)")}


@functools.lru_cache(maxsize=1)
def _current_entries_columns() -> frozenset:
    """Colunas de `entries` na DDL corrente, lidas da própria DDL num banco
    de memória. Uma lista escrita à mão desatualizaria no dia em que a v9
    ganhasse uma coluna — que é exatamente o dia em que `stamp_if_unversioned`
    voltaria a carimbar schema velho."""
    probe = sqlite3.connect(":memory:")
    try:
        probe.executescript(_SCHEMA)
        return frozenset(entries_columns(probe))
    finally:
        probe.close()


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(_SCHEMA)
    con.execute(_FTS)
    con.execute(_CURATED_FTS)
    if "regime" in entries_columns(con):
        for ddl in _ENTRIES_INDEXES:
            con.execute(ddl)
    con.commit()


def schema_state(con: sqlite3.Connection) -> str:
    """Classifica o carimbo de versão do índice, sem julgar: `"current"`,
    `"unstamped"` (nunca reindexado) ou `"mismatch"` (schema antigo).

    Separar a leitura do veredito é o que deixa cada chamador decidir
    sozinho: `check_schema` transforma em erro, `query` cura o caso
    `unstamped` reindexando. SELECT puro no meta, zero efeito colateral.
    """
    row = con.execute(
        "SELECT value FROM meta WHERE key='index_schema_version'").fetchone()
    if row is None:
        return "unstamped"
    return "current" if str(row[0]) == str(INDEX_SCHEMA_VERSION) else "mismatch"


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

    "Sem carimbo" não é sinônimo de "recém-inicializado": um índice de
    versão anterior que nunca ganhou a linha de versão também cai aqui.
    Por isso a tolerância de `require_reindexed=False` exige a prova
    estrutural — as colunas de `entries` conterem as da DDL corrente.
    Sem ela, o tick escreveria e só descobriria a incompatibilidade no
    meio da curadoria, num `OperationalError: table entries has no
    column named agent` cru, com parte do trabalho já gravada.

    Leitura pura (meta + PRAGMA) — zero efeito colateral. Público
    (promovido de `query._check_schema`) pra `tick` também poder validar
    na entrada do run, antes de rodar near-dup contra um índice sem
    coluna `shingles` (v4).
    """
    state = schema_state(con)
    if state == "current":
        return
    if state == "unstamped" and not require_reindexed and _entries_fits(con):
        return
    raise IndexSchemaError(
        "índice ausente ou em schema antigo — rode `neurata reindex`")


def _entries_fits(con: sqlite3.Connection) -> bool:
    """`entries` comporta a DDL corrente? Tabela ausente conta como sim:
    é home recém-inicializado, ausência de dados e não schema velho —
    quem escreve cria antes de gravar."""
    cols = entries_columns(con)
    return not cols or _current_entries_columns() <= cols


PROVENANCE_COLS = ("agent", "session", "origin")


def migrate_if_needed(con: sqlite3.Connection,
                      home: NeurataHome) -> "int | None":
    """Migra o índice pro schema corrente, se houver caminho. Devolve a
    versão resultante, ou `None` quando não havia o que migrar.

    Entrada pública dos chamadores que ainda não detêm o lock: pega o
    `IndexLock` e repete a checagem sob ele, porque entre o SELECT barato
    e o lock outro processo pode ter migrado (ou reindexado) o mesmo
    índice. `LockHeldError` propaga — migrar é escrita, e duas escritas
    concorrentes sobre a mesma DDL não têm resultado definido.

    Pré-condição: `con` sem transação aberta (o caso logo após `connect`).
    """
    if schema_state(con) != "mismatch":
        return None
    with IndexLock(home):
        return _migrate_locked(con, home)


def _migrate_locked(con: sqlite3.Connection,
                    home: NeurataHome) -> "int | None":
    """Corpo da migração, assumindo o lock **já detido** pelo chamador.

    Existe separado porque `tick` roda inteiro dentro de um `IndexLock`:
    chamar a entrada pública lá dentro pediria o lock que ele mesmo tem e
    morreria em `LockHeldError` — o deadlock que uma reentrância aparente
    esconde melhor do que dois nomes explícitos.

    Encadeia os passos disponíveis: devolve a versão final alcançada, não
    a do primeiro passo.
    """
    if schema_state(con) != "mismatch":
        return None
    stamped = _stamped_version(con)
    if stamped is None or stamped not in _MIGRATIONS:
        raise IndexSchemaError(
            "índice em schema antigo sem caminho de migração — rode "
            "`neurata reindex`")

    # Encadeia enquanto houver passo (D6): um índice v7 tem que chegar ao
    # schema corrente numa chamada, senão quem nunca migrou da v1.0 fica
    # preso no meio do caminho. Invariante de progresso estrito: passo que
    # não aumenta o carimbo é laço infinito sob IndexLock — que trava o
    # acervo inteiro —, então vira erro na primeira volta.
    while (step := _MIGRATIONS.get(stamped)) is not None:
        step(con, home)
        current = _stamped_version(con)
        if current is None or current <= stamped:
            raise IndexSchemaError(
                f"migração v{stamped} não avançou o carimbo "
                f"(agora: {current}) — passo defeituoso, índice parado "
                "onde estava; rode `neurata reindex`")
        stamped = current
    return stamped


def _stamped_version(con: sqlite3.Connection) -> "int | None":
    """Versão carimbada como inteiro; `None` se ausente ou ilegível — que
    é o mesmo veredito prático: não há de onde migrar."""
    row = con.execute(
        "SELECT value FROM meta WHERE key='index_schema_version'").fetchone()
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def _v7_to_v8(con: sqlite3.Connection, home: NeurataHome) -> None:
    """v7 → v8: pendura `agent`/`session`/`origin` e reconstrói a
    procedência dos curados a partir do frontmatter — os arquivos são a
    fonte de verdade; o índice sempre foi descartável.

    Tudo numa transação explícita, inclusive a DDL (sqlite reverte
    `ALTER TABLE`): um crash no meio não pode deixar colunas penduradas
    sem carimbo, estado em que o próximo INSERT acharia o schema novo.
    O carimbo é a penúltima escrita — nunca existe v8 anunciado com
    colunas vazias.
    """
    con.execute("BEGIN IMMEDIATE")
    try:
        existing = entries_columns(con)
        for col in PROVENANCE_COLS:
            if col not in existing:
                con.execute(f"ALTER TABLE entries ADD COLUMN {col} TEXT")
        for rowid, path in con.execute(
                "SELECT rowid, path FROM entries"
                " WHERE regime='curated'").fetchall():
            agent, session, origin = _grain_provenance(home, path)
            con.execute("UPDATE entries SET agent=?, session=?, origin=?"
                        " WHERE rowid=?", (agent, session, origin, rowid))
        con.execute("INSERT OR REPLACE INTO meta VALUES"
                    " ('index_schema_version', '8')")
        con.commit()
    except BaseException:
        con.rollback()
        raise


def _grain_provenance(home: NeurataHome,
                      path: str) -> "tuple[str | None, str | None, str | None]":
    """Procedência de um grão do disco; tudo `None` se o arquivo sumiu ou
    não parseia. Um `.md` apagado na mão não pode segurar a migração do
    índice inteiro refém — a linha fica sem procedência e `reindex`
    conserta quando o arquivo voltar."""
    try:
        meta, _ = frontmatter.parse(
            (home.root / path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return (None, None, None)
    return provenance(meta)


_MIGRATIONS = {7: _v7_to_v8}


def load_shingle_sets(con: sqlite3.Connection) -> "dict[str, frozenset]":
    """{entry.id: frozenset(shingle-hashes)} pra near-dup em Task 4."""
    rows = con.execute("SELECT id, shingles FROM entries").fetchall()
    return {eid: frozenset(json.loads(shingles)) for eid, shingles in rows}


def drop_schema(con: sqlite3.Connection) -> None:
    con.execute("DROP TABLE IF EXISTS curated_fts")
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
        except FileExistsError as exc:
            if self._is_stale():
                self.path.unlink(missing_ok=True)
                self._acquire()
            else:
                raise LockHeldError(f"lock ativo: {self.path}") from exc
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
