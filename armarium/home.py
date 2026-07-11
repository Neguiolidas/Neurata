"""armarium/home.py — layout de storage e config. Arquivos são a verdade."""
import json
import os
from pathlib import Path

SCHEMA_VERSION = 1
CONTRACT_VERSION = 1

_DIRS = ("library", "inbox", "archive", "quarantine", "logs")


class ArmariumHome:
    def __init__(self, root: "Path | str | None" = None):
        raw = root or os.environ.get("ARMARIUM_HOME") or "~/.armarium"
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
        path = self.logs / f"{name}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_log(self, name: str) -> list[dict]:
        path = self.logs / f"{name}.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out
