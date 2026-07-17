"""neurata/cli.py — fachada CLI. Contrato JSON versionado."""
import argparse
import dataclasses
import json
import sys

from neurata.deposit import DepositError, deposit
from neurata.doctor import exit_code, run_checks
from neurata.home import CONTRACT_VERSION, NeurataHome
from neurata.config import ConfigError
from neurata.indexdb import FTS5MissingError, LockHeldError
from neurata.query import QueryError, query
from neurata.reindex import reindex


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
    try:
        home = NeurataHome()
        home.init()
        result, rc = _dispatch(args, home)
    except (ConfigError, DepositError, FTS5MissingError, LockHeldError,
            QueryError, OSError) as exc:
        _emit_error(args, exc)
        return 2
    except Exception as exc:  # nunca vaza traceback pela CLI
        _emit_error(args, exc)
        return 2
    _emit(args, result, rc)
    return rc


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
    raise AssertionError(f"comando desconhecido: {args.command}")


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
    qry.add_argument("q", help='texto e/ou facets (type:/tag:/env:/project:)')
    qry.add_argument("--limit", type=int, default=10)
    qry.add_argument("--json", action="store_true",
                     default=argparse.SUPPRESS)

    for name, hlp in (("reindex", "rebuild total do índice"),
                      ("doctor", "self-check com remediação")):
        p = sub.add_parser(name, help=hlp)
        p.add_argument("--json", action="store_true",
                       default=argparse.SUPPRESS)
    return parser


def _emit(args: argparse.Namespace, result: dict, rc: int = 0) -> None:
    if getattr(args, "json", False):
        # doctor pode terminar rc != 0 sem lançar exceção (checks "fail");
        # nesse caso o envelope precisa refletir a falha em `ok`, não só
        # no exit code — senão consumidores que checam só `ok` leem êxito.
        ok = rc == 0 if args.command == "doctor" else True
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
