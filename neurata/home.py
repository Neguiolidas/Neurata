"""neurata/home.py — layout de storage e config. Arquivos são a verdade."""
import json
import os
import re
from pathlib import Path

SCHEMA_VERSION = 1
CONTRACT_VERSION = 4  # v1.2: envelope do tick ganha a linha `absorve:`

_DIRS = ("library", "inbox", "archive", "quarantine", "logs")

_LOG_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _safe_log_name(name: str) -> str:
    """Valida `name` como identificador simples de log (sem path/traversal).

    Rejeita path absoluto, componente `..`, separadores de diretório e
    qualquer caractere fora de `[A-Za-z0-9_-]` — `name` vira `logs/{name}.jsonl`
    direto, sem passar por `Path` normalizador.
    """
    if not _LOG_NAME_RE.match(name):
        raise ValueError(f"invalid log name: {name!r}")
    return name


class NeurataHome:
    def __init__(self, root: "Path | str | None" = None):
        if root is not None:
            raw = root
        else:
            raw = os.environ.get("NEURATA_HOME") or "~/.neurata"
        self.root = Path(raw).expanduser()
        self.library = self.root / "library"
        self.inbox = self.root / "inbox"
        self.archive = self.root / "archive"
        self.quarantine = self.root / "quarantine"
        self.logs = self.root / "logs"
        self.index_path = self.root / "index.db"
        self.config_path = self.root / "config.json"

    def init(self) -> None:
        for d in _DIRS:
            (self.root / d).mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            self.config_path.write_text(json.dumps(
                {"schema_version": SCHEMA_VERSION}, indent=2) + "\n")

    def load_config(self) -> dict:
        return json.loads(self.config_path.read_text())

    def append_log(self, name: str, record: dict) -> None:
        path = self.logs / f"{_safe_log_name(name)}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_log(self, name: str) -> list[dict]:
        path = self.logs / f"{_safe_log_name(name)}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
