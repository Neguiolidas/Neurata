"""neurata/doctor.py — self-check com remediação. Nunca degrada em silêncio."""
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from neurata import archive, snapshot, usage
from neurata import config as query_config
from neurata.frontmatter import FrontmatterError
from neurata.frontmatter import parse as parse_frontmatter
from neurata.home import SCHEMA_VERSION, NeurataHome
from neurata.indexdb import INDEX_SCHEMA_VERSION, fts5_available

_ARCHIVE_SAMPLE = 20
# `git log` é O(commits); cap alto o bastante pra "contagem exata" na
# prática (ninguém tem >10k snapshots), sem varrer history absurdo.
_SNAPSHOT_LOG_CAP = 10_000


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
    checks.append(_archive(home))
    checks.append(_usage(home))
    checks.append(_last_tick(home))
    checks.append(_gate(home))
    checks.append(_snapshot(home))
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


def _indexed_hashes(home: NeurataHome) -> "dict[str, str]":
    """path relativo -> content_hash que o índice conhece."""
    if not home.index_path.exists():
        return {}
    con = sqlite3.connect(home.index_path)
    try:
        return {row[0]: row[1] or ""
                for row in con.execute(
                    "SELECT path, content_hash FROM entries")}
    except sqlite3.DatabaseError:  # índice ausente/corrompido: outro check
        return {}
    finally:
        con.close()


def _body_hash(path) -> "str | None":
    """sha256 do corpo (sem frontmatter), no mesmo formato do índice.
    None quando o arquivo não dá pra ler/parsear — aí não há como provar
    que está indexado."""
    try:
        _, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, FrontmatterError):
        return None
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _freshness(home: NeurataHome) -> Check:
    last = _meta(home, "last_reindex")
    if last is None:
        return Check("index-freshness", "warn", "sem last_reindex",
                     "rode `neurata reindex`")
    last_ts = datetime.fromisoformat(last).timestamp()
    # mtime é pista, não veredito: o tick reescreve na library arquivos que
    # ele mesmo indexou (só `reindex` carimba last_reindex), então "mais
    # novo que o carimbo" não implica "fora do índice". Confirma pelo hash
    # do corpo — quem bate com o índice não está stale. Sem isso, um tick
    # que cruza a virada do segundo faz o doctor sair 1 sem nada errado.
    # last_reindex tem resolução de segundos — floor dos dois lados.
    suspects = [p for base in (home.library, home.inbox)
                for p in base.rglob("*.md")
                if int(p.stat().st_mtime) > int(last_ts)]
    known = _indexed_hashes(home) if suspects else {}
    stale = []
    for path in suspects:
        rel = str(path.relative_to(home.root))
        disk = _body_hash(path)
        # disk is None: ilegível/frontmatter quebrado — não dá pra provar
        # que o índice está em dia com ele; conta como stale.
        if disk is None or known.get(rel) != disk:
            stale.append(rel)
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


def _archive(home: NeurataHome) -> Check:
    shas: list[str] = []
    for base in (home.library, home.inbox):
        for path in sorted(base.rglob("*.md")):
            try:
                meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, FrontmatterError):
                continue
            derived_from = meta.get("derived_from")
            if derived_from:
                shas.append(str(derived_from))
    if not shas:
        return Check("archive", "ok", "nenhuma entrada compactada")
    sample = shas[:_ARCHIVE_SAMPLE]
    bad = []
    for sha in sample:
        try:
            archive.get(home, sha)
        except (archive.BadShaError, archive.ArchiveMissingError,
                archive.ArchiveCorruptError) as exc:
            bad.append(f"{sha}: {exc}")
    if bad:
        return Check("archive", "fail",
                     f"{len(bad)}/{len(sample)} blob(s) inválido(s): "
                     + "; ".join(bad),
                     "restaure archive/ de backup — o Neurata nunca "
                     "reescreve blobs, então corrupção só vem de fora")
    detail = f"{len(sample)}/{len(shas)} blob(s) verificado(s), ok"
    return Check("archive", "ok", detail)


def _usage(home: NeurataHome) -> Check:
    path = home.logs / "usage.jsonl"
    if not path.exists():
        return Check("usage", "ok", "usage.jsonl ausente (nenhum uso "
                     "registrado ainda)")
    data = usage.read_usage(home)
    corrupt = data["corrupt_lines"]
    if corrupt:
        return Check("usage", "warn",
                     f"{corrupt} linha(s) corrompida(s) em usage.jsonl",
                     "linhas corrompidas são só puladas — sem ação "
                     "obrigatória, mas investigue se o volume crescer")
    return Check("usage", "ok", f"{len(data['entries'])} entrada(s) "
                 "com uso registrado")


_TICK_MAX_AGE = timedelta(hours=2)  # 2× o intervalo de cron (0 * * * *)


def _last_tick(home: NeurataHome) -> Check:
    data = usage.read_invocations(home)
    ticks = [e["ts"] for e in data["entries"] if e.get("cmd") == "tick"]
    if not ticks:
        return Check("last-tick", "warn", "nenhum tick registrado ainda",
                     "configure o cron do tick: `0 * * * * neurata tick`")
    last = max(ticks)
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
    except ValueError:
        return Check("last-tick", "warn", f"ts de tick ilegível: {last}",
                     "usage.log pode estar corrompido — investigue")
    if age > _TICK_MAX_AGE:
        hours = int(age.total_seconds() // 3600)
        return Check("last-tick", "warn",
                     f"último tick há ~{hours}h ({last})",
                     "cron do tick parado? confira o crontab")
    return Check("last-tick", "ok", f"último tick={last}")


_GATE_WINDOW_DAYS = 14
_GATE_TARGET_DAYS = 10


def _gate(home: NeurataHome) -> Check:
    """Gate de dogfooding da v1.0: >= 10 dias distintos com >= 1 comando
    real (não-tick) numa janela corrida de 14 dias. Sempre `ok` — é um
    medidor de progresso, não um erro: parcial mostra `N/10`, atingido
    mostra `PASSED`. Nunca `warn`, nunca `fail`. Lê o log de invocações
    (usage.log); ts ilegível ou tick não contam."""
    entries = usage.read_invocations(home)["entries"]
    dates = set()
    for e in entries:
        if e.get("cmd") == "tick":
            continue
        try:
            dates.add(datetime.fromisoformat(e["ts"]).date())
        except (ValueError, TypeError):
            continue
    days = sorted(dates)
    best = 0
    for start in days:
        end = start + timedelta(days=_GATE_WINDOW_DAYS - 1)
        best = max(best, sum(1 for d in days if start <= d <= end))
    if best >= _GATE_TARGET_DAYS:
        return Check("gate", "ok",
                     f"PASSED ({best} dias distintos em janela de "
                     f"{_GATE_WINDOW_DAYS}d)")
    return Check("gate", "ok",
                 f"{best}/{_GATE_TARGET_DAYS} dias distintos (melhor janela "
                 f"de {_GATE_WINDOW_DAYS}d) — precisa de uso real contínuo")


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


def _snapshot(home: NeurataHome) -> Check:
    """Saúde do snapshot git (spec §8). Nunca `fail`: snapshot é
    best-effort, degradação graciosa — git ausente ou repo não-init são
    `warn` informativos, não erro. Também surface config incoerente
    (`auto_push` ligado sem `remote`) e mudanças ainda não-commitadas.
    Não muta nada: só lê (`.git` existe? `git log`? `status`?), nunca
    inicializa o repo (isso é trabalho do primeiro `tick`)."""
    try:
        snap_cfg = home.load_config().get("snapshot", {})
    except (OSError, ValueError):
        snap_cfg = {}  # _config já reporta config ilegível — aqui degrada
    auto_push = snap_cfg.get("auto_push", False)
    remote = snap_cfg.get("remote")

    if not snapshot.git_available():
        return Check("snapshot", "warn", "git não disponível",
                     "instale git para habilitar snapshots — opcional: sem "
                     "git o Neurata roda normalmente, só não versiona")
    if auto_push and not remote:
        return Check("snapshot", "warn",
                     "auto_push ligado sem remote configurado",
                     "configure `neurata snapshot --set-remote <url>` ou "
                     "desligue snapshot.auto_push em config.json")
    if not (home.library / ".git").exists():
        # warn (spec §8), mas self-heal-aware: dá ação imediata E avisa
        # que o próximo tick cura sozinho — doctor não muta (nenhum check
        # inicializa nada; init é trabalho do tick/snapshot).
        return Check("snapshot", "warn", "repo não inicializado",
                     "rode `neurata snapshot` pra versionar agora, ou "
                     "deixe o próximo `neurata tick` inicializar sozinho "
                     "(self-heal automático)")
    snaps = snapshot.list_snapshots(home, limit=_SNAPSHOT_LOG_CAP)
    head = snaps[0]["sha"] if snaps else "nenhum"
    detail = f"{len(snaps)} snapshot(s), HEAD {head}"
    if snapshot.has_changes(home):
        return Check("snapshot", "warn", detail + ", mudanças não-commitadas",
                     "mudanças serão commitadas no próximo `neurata tick`")
    return Check("snapshot", "ok", detail)
