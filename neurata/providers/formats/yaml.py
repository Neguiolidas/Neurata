"""Adapter `yaml` — `.yaml`/`.yml` (agent cards, prompts, OpenAPI, CI…).

**PyYAML é opcional.** `neurata` declara `dependencies = []` e não vai
ganhar uma dep transitiva por causa de um adapter: se `yaml` não estiver
instalado, o título sai por regex. Os dois caminhos usam a MESMA ordem
de precedência (`name` > `id` > `info.name`) pra que instalar/desinstalar
PyYAML não mude o título — e portanto não gere item duplicado no índice.

Nota: `import yaml` aqui é absoluto (Python 3), então resolve pro PyYAML
de topo, não pra este módulo homônimo.
"""
import re
from pathlib import Path

from neurata.providers.generic import Scanned, oneline

try:  # pragma: no cover - depende do ambiente
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

#: Chaves de topo, na ordem de precedência. `re.M` + `^\S` garante nível
#: raiz: `  name:` indentado é campo de outro objeto, não título do doc.
_TOP_KEY = r"^{key}:[ \t]*(?:\"|')?(.+?)(?:\"|')?[ \t]*$"
_NAME_RE = re.compile(_TOP_KEY.format(key="name"), re.MULTILINE)
_ID_RE = re.compile(_TOP_KEY.format(key="id"), re.MULTILINE)
_DESC_RE = re.compile(_TOP_KEY.format(key="description"), re.MULTILINE)


def _from_regex(text: str, pattern: "re.Pattern[str]") -> "str | None":
    match = pattern.search(text)
    return match.group(1) if match else None


def _first_str(*values: object) -> "str | None":
    """Primeiro valor que é string não-vazia (YAML devolve int/None/dict)."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _from_yaml(text: str) -> "tuple[str | None, str | None]":
    """(title, description) via PyYAML, ou (None, None) se não der."""
    try:
        doc = _yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - YAML torto cai pro regex
        return None, None
    if not isinstance(doc, dict):
        return None, None
    info = doc.get("info")
    info = info if isinstance(info, dict) else {}
    title = _first_str(doc.get("name"), doc.get("id"), info.get("name"))
    desc = _first_str(doc.get("description"), info.get("description"))
    return title, desc


def parse(path: Path, text: str) -> "Scanned | None":
    title = desc = None
    if _yaml is not None:
        title, desc = _from_yaml(text)
    if title is None:
        title = _first_str(_from_regex(text, _NAME_RE),
                           _from_regex(text, _ID_RE))
    if desc is None:
        desc = _from_regex(text, _DESC_RE)
    return Scanned(
        name=oneline(title or path.stem, 120) or path.stem,
        description=oneline(desc or text),
        body=text,
        source_path=str(path),
        fmt="yaml",
    )
