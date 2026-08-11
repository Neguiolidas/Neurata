"""neurata/cli.py — fachada CLI. Contrato JSON versionado."""
import argparse
import contextlib
import dataclasses
import json
import sys
import time
from pathlib import Path

from neurata.compact import compact
from neurata.config import ConfigError
from neurata.config import load as load_config
from neurata.deposit import DepositError, deposit
from neurata.doctor import exit_code, run_checks
from neurata.entryref import EntryAmbiguousError, EntryNotFoundError
from neurata.expand import _GRAINS, ExpandError, expand
from neurata.harvest import REGISTRY, validate_target
from neurata.harvest import harvest as run_harvest
from neurata.home import CONTRACT_VERSION, NeurataHome
from neurata.indexdb import FTS5MissingError, IndexSchemaError, LockHeldError
from neurata.providers.generic import FORMATS
from neurata.query import QueryError, query
from neurata.reindex import reindex
from neurata.shelf import conflicts as shelf_conflicts
from neurata.shelf import insights as shelf_insights
from neurata.shelf import inventory as shelf_inventory
from neurata.snapshot import (
    SnapshotError,
    commit_manual,
    ensure_repo,
    list_snapshots,
    push,
    restore,
    restore_dry_run,
    set_remote,
)
from neurata.tick import TickStructuralError, curate_tick
from neurata.usage import log_invocation


class UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse sem sys.exit: bad args viram UsageError → envelope."""

    def error(self, message: str) -> "None":  # type: ignore[override]
        raise UsageError(message)


def main(argv: "list[str] | None" = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as exc:
        _emit_usage_error(argv, exc)
        return 2
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    home = None
    rc = 2
    started = time.monotonic()
    try:
        home = NeurataHome()
        home.init()
        result, rc = _dispatch(args, home)
        _emit(args, result, rc)
    except TickStructuralError as exc:
        _emit_error(args, exc)
        rc = 1
    except (ConfigError, DepositError, EntryAmbiguousError,
            EntryNotFoundError, ExpandError, FTS5MissingError,
            IndexSchemaError, LockHeldError, QueryError, SnapshotError,
            OSError) as exc:
        _emit_error(args, exc)
        rc = 2
    # Barreira final: a CLI nunca vaza traceback. Qualquer exceção não
    # prevista vira erro JSON com rc=2 — por isso o catch é largo de
    # propósito, e não um descuido.
    except Exception as exc:  # noqa: BLE001
        _emit_error(args, exc)
        rc = 2
    finally:
        # Loga a invocação assim que houve home (best-effort §gate v0.9):
        # falha aqui NUNCA altera o exit code nem propaga.
        if home is not None:
            duration_ms = int((time.monotonic() - started) * 1000)
            with contextlib.suppress(Exception):
                log_invocation(home, args.command, duration_ms, rc == 0)
    return rc


def _harvest_kwargs(args: argparse.Namespace) -> dict:
    """`harvest <source>` → kwargs de `harvest()`. Levanta `UsageError`.

    `source` é provider nomeado OU diretório; nome de provider ganha do
    diretório homônimo (o default `claude-code` não pode depender do cwd).
    """
    if args.source in REGISTRY:
        extra = ("--target" if args.target else
                 "--format" if args.fmt != "auto" else None)
        if extra:
            raise UsageError(
                f"{extra} só vale ao colher um diretório; "
                f"'{args.source}' é um provider nomeado")
        return {"target": args.source}

    source_dir = Path(args.source).expanduser()
    if not source_dir.is_dir():
        raise UsageError(
            f"fonte desconhecida: '{args.source}' não é um provider "
            f"({', '.join(REGISTRY)}) nem um diretório existente")
    target = args.target or source_dir.resolve().name
    try:
        validate_target(target)
    except ValueError as e:
        raise UsageError(f"rótulo inválido: {e}; passe --target explícito") from e
    return {"target": target, "source_dir": source_dir, "fmt": args.fmt}


def _dispatch(args: argparse.Namespace, home: NeurataHome) -> tuple[dict, int]:
    if args.command == "deposit":
        text = args.text
        if text == "-":
            text = sys.stdin.read()
        result = deposit(home, content=text,
                         file=args.file, title=args.title, dtype=args.type,
                         denv=args.env, agent=args.agent,
                         session=args.session)
        return result, 0
    if args.command == "reindex":
        return reindex(home), 0
    if args.command == "query":
        return query(home, args.q, limit=args.limit), 0
    if args.command == "doctor":
        checks = run_checks(home)
        return ({"checks": [dataclasses.asdict(c) for c in checks]},
                exit_code(checks))
    if args.command == "compact":
        return compact(home, args.ref), 0
    if args.command == "expand":
        return expand(home, args.ref, grain=args.grain,
                      restore=args.restore), 0
    if args.command == "shelf":
        if args.conflicts:
            return shelf_conflicts(home), 0
        if args.insights:
            return shelf_insights(home), 0
        return shelf_inventory(home), 0
    if args.command == "tick":
        report = curate_tick(home, budget=args.budget)
        # auto_push §7.5: FORA do IndexLock (curate_tick já soltou o
        # lock ao retornar) — rede/lento não pode alongar a seção
        # crítica. Nunca propaga: falha vira log, tick permanece verde.
        cfg = load_config(home)
        snap_cfg = cfg["snapshot"]
        if snap_cfg["auto_push"] and snap_cfg["remote"] and report.snapshot:
            try:
                push_result = push(home, remote_url=snap_cfg["remote"])
                if not push_result.get("ok"):
                    home.append_log("snapshot", {
                        "tick": report.tick,
                        "push_error": push_result.get("error")})
            # push é rede: qualquer falha vira log e o tick permanece
            # verde. Catch largo é o desenho (ver comentário acima).
            except Exception as exc:  # noqa: BLE001
                home.append_log("snapshot", {"tick": report.tick,
                                             "push_error": str(exc)})
        result = dataclasses.asdict(report)
        return result, (2 if report.errors else 0)
    if args.command == "harvest":
        harvest_report = run_harvest(home, **_harvest_kwargs(args))
        return dataclasses.asdict(harvest_report), 0
    if args.command == "snapshot":
        return _dispatch_snapshot(args, home)
    raise AssertionError(f"comando desconhecido: {args.command}")


def _dispatch_snapshot(args: argparse.Namespace,
                       home: NeurataHome) -> "tuple[dict, int]":
    if args.set_remote:
        return _snapshot_set_remote(args, home)
    if args.list:
        return _snapshot_list(args, home)
    if args.restore:
        return _snapshot_restore(args, home)
    if args.push:
        return _snapshot_push(args, home)
    return commit_manual(home), 0


def _snapshot_list(args: argparse.Namespace,
                   home: NeurataHome) -> "tuple[dict, int]":
    return {"ok": True,
           "snapshots": list_snapshots(home, limit=args.limit)}, 0


def _snapshot_restore(args: argparse.Namespace,
                      home: NeurataHome) -> "tuple[dict, int]":
    ref = args.restore
    if not args.yes:
        result = restore_dry_run(home, ref)
        return result, (2 if result.get("error") else 1)
    result = restore(home, ref)
    return result, (0 if result.get("ok") else 2)


def _snapshot_push(args: argparse.Namespace,
                   home: NeurataHome) -> "tuple[dict, int]":
    cfg = load_config(home)
    result = push(home, remote_url=cfg["snapshot"].get("remote"))
    return result, (0 if result.get("ok") else 2)


def _snapshot_set_remote(args: argparse.Namespace,
                         home: NeurataHome) -> "tuple[dict, int]":
    ensure_repo(home)
    set_remote(home, args.set_remote)
    _persist_snapshot_remote(home, args.set_remote)
    return {"ok": True, "remote": args.set_remote}, 0


def _persist_snapshot_remote(home: NeurataHome, url: str) -> None:
    """Grava `snapshot.remote` no `config.json` preservando as demais
    chaves. NÃO usa `config.load()` + replace: esse dict funde com
    `DEFAULTS` e omite `schema_version` de propósito (ver config.py) —
    escrevê-lo de volta como está apagaria `schema_version` do arquivo."""
    raw: dict = {}
    if home.config_path.exists():
        raw = json.loads(home.config_path.read_text(encoding="utf-8"))
    snap = dict(raw.get("snapshot") or {})
    snap["remote"] = url
    raw["snapshot"] = snap
    home.config_path.write_text(
        json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="neurata",
        description="Neurata — the living knowledge layer for an agent's "
                    "environment.")
    parser.add_argument("--json", action="store_true",
                        help="saída JSON (contrato versionado)")
    sub = parser.add_subparsers(dest="command")

    dep = sub.add_parser("deposit", help="captura crua → inbox")
    dep.add_argument("text", nargs="?", default=None,
                     help="conteúdo (ou '-' para stdin)")
    dep.add_argument("--file", type=str, default=None)
    dep.add_argument("--title", default=None)
    dep.add_argument("--type", default="note")
    dep.add_argument("--env", default="generic")
    dep.add_argument("--agent", default=None)
    dep.add_argument("--session", default=None)
    # default=SUPPRESS: a flag do subparser só toca o namespace quando
    # presente — senão o default False do subparser sobrescreveria o
    # --json global já parseado (`neurata --json deposit x`).
    dep.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    qry = sub.add_parser("query", help="busca lexical + grafo → cards")
    qry.add_argument(
        "q", help='texto e/ou facets (type:/tag:/env:/project:/regime:)')
    qry.add_argument("--limit", type=int, default=10)
    qry.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    for name, hlp in (("reindex", "rebuild total do índice"),
                      ("doctor", "self-check com remediação")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--json", action="store_true",
                       default=argparse.SUPPRESS)

    cpt = sub.add_parser("compact",
                         help="corpo->summary, full pro archive (utilitário)")
    cpt.add_argument("ref", help="id ou slug")
    cpt.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    exp = sub.add_parser("expand", help="grão maior sob demanda")
    exp.add_argument("ref", help="id ou slug")
    exp.add_argument("--grain", choices=_GRAINS,
                     default="full")
    exp.add_argument("--restore", action="store_true",
                     help="restaura o full do archive, remove derived_from")
    exp.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    shf = sub.add_parser("shelf", help="inventário ordenado por score desc")
    shf.add_argument("--insights", action="store_true",
                     help="top consultados/expands, nunca-consultados")
    shf.add_argument("--conflicts", action="store_true",
                     help="conflicts_with + colisões id/slug")
    shf.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    tck = sub.add_parser("tick",
                         help="inbox → library, curadoria mecânica "
                              "(utilitário)")
    tck.add_argument("--budget", type=int, default=None,
                     help="corta a N itens do inbox (default: tudo)")
    tck.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    hrv = sub.add_parser("harvest",
                         help="providers externos → inbox (utilitário)")
    hrv.add_argument("source", nargs="?", default="claude-code",
                     help=f"provider ({', '.join(REGISTRY)}) ou um diretório "
                          f"a colher (default: claude-code)")
    hrv.add_argument("--format", dest="fmt", default="auto",
                     choices=FORMATS,
                     help="formato dos arquivos ao colher um diretório "
                          "(default: auto — detecta por arquivo)")
    hrv.add_argument("--target", default=None,
                     help="rótulo dos itens colhidos de um diretório "
                          "(default: nome do diretório)")
    hrv.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    snp = sub.add_parser("snapshot",
                         help="git audit da library — commit manual, "
                              "--list, --restore, --push, --set-remote")
    snp.add_argument("--list", action="store_true",
                     help="lista snapshots recentes")
    snp.add_argument("-n", "--limit", type=int, default=20,
                     help="máximo de snapshots com --list (default: 20)")
    snp.add_argument("--restore", metavar="REF", default=None,
                     help="restaura a library pro estado de REF")
    snp.add_argument("--yes", action="store_true",
                     help="confirma --restore (senão só mostra dry-run)")
    snp.add_argument("--push", action="store_true",
                     help="push explícito pro remote 'neurata'")
    snp.add_argument("--set-remote", metavar="URL", default=None,
                     help="configura o remote de push e grava no config")
    snp.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)
    return parser


def _emit(args: argparse.Namespace, result: dict, rc: int = 0) -> None:
    if getattr(args, "json", False):
        # doctor e tick podem terminar rc != 0 sem lançar exceção (doctor:
        # checks "fail"; tick: item errors) — nesses casos o envelope
        # precisa refletir a falha em `ok`, não só no exit code, senão
        # consumidores que checam só `ok` leem êxito.
        ok = (rc == 0 if args.command in ("doctor", "tick", "harvest",
                                          "snapshot") else True)
        print(json.dumps({"contract_version": CONTRACT_VERSION, "ok": ok,
                          "command": args.command, "result": result},
                         ensure_ascii=False))
        return
    if args.command == "query":
        for c in result["results"]:
            score = "-" if c["score"] is None else f"{c['score']:.4f}"
            line = f"{score} {c['slug']} — {c['title']}"
            if c.get("snippet"):
                line += f" ({c['snippet'][:100]})"
            print(line)
        if not result["results"]:
            print("(sem resultados)")
        return
    if args.command == "doctor":
        for c in result["checks"]:
            mark = {"ok": "✓", "warn": "!", "fail": "✗"}[c["status"]]
            line = f"{mark} {c['name']}: {c['detail']}"
            if c["remedy"]:
                line += f" — {c['remedy']}"
            print(line)
        return
    if args.command == "expand" and "text" in result:
        print(result["text"])
        return
    if args.command == "harvest":
        print(f"harvested={result['harvested']} updated={result['updated']} "
             f"removed={result['removed']} skipped={len(result['skipped'])}")
        return
    if args.command == "snapshot":
        if "snapshots" in result:
            for s in result["snapshots"]:
                print(f"{s['sha']} {s['ts']} {s['subject']}")
            if not result["snapshots"]:
                print("(sem snapshots)")
            return
        if result.get("dry_run"):
            print(f"dry-run restore -> {result['restored_to']} "
                 f"(use --yes pra confirmar)")
            print(result["would_change"] or "(sem diferenças)")
            if result["dirty"]:
                print("aviso: mudanças não-commitadas viram autosave")
            return
        if "error" in result:
            print(f"erro: {result['error']}")
            return
        print(" ".join(f"{k}={v}" for k, v in result.items()
                       if not isinstance(v, (list, dict))))
        return
    if args.command == "shelf":
        if args.conflicts:
            for c in result["conflicts_with"]:
                print(f"conflicts_with {c['id']} {c['path']}: "
                     f"{', '.join(c['conflicts_with'])}")
            for c in result["collisions"]:
                print(f"colisão {c.get('reason')}: {c}")
            if not result["conflicts_with"] and not result["collisions"]:
                print("(sem conflitos)")
            return
        if args.insights:
            print(f"total_conflitos={result['total_conflitos']}")
            for label in ("top_consultados", "top_expands",
                         "nunca_consultados"):
                print(f"-- {label} --")
                for c in result[label]:
                    print(f"{c['slug']} — {c['title']}")
            return
        for c in result["items"]:
            flag = " [candidato-arquivamento]" if c[
                "candidato_arquivamento"] else ""
            print(f"{c['score']:.4f} {c['slug']} — {c['title']}{flag}")
        return
    print(" ".join(f"{k}={v}" for k, v in result.items()
                   if not isinstance(v, (list, dict))))


def _emit_error(args: argparse.Namespace, exc: Exception) -> None:
    if getattr(args, "json", False):
        print(json.dumps({"contract_version": CONTRACT_VERSION, "ok": False,
                          "command": args.command,
                          "error": {"code": type(exc).__name__,
                                    "message": str(exc)}},
                         ensure_ascii=False))
    else:
        print(f"erro ({type(exc).__name__}): {exc}", file=sys.stderr)


def _emit_usage_error(argv: "list[str] | None", exc: Exception) -> None:
    args = argv if argv is not None else sys.argv[1:]
    if "--json" in args:
        print(json.dumps({"contract_version": CONTRACT_VERSION, "ok": False,
                          "command": None,
                          "error": {"code": "UsageError",
                                    "message": str(exc)}},
                         ensure_ascii=False))
    else:
        print(f"erro (UsageError): {exc}", file=sys.stderr)
