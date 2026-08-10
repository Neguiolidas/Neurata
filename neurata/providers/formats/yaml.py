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
from functools import cache
from pathlib import Path

from neurata.providers.generic import Scanned, oneline

try:  # pragma: no cover - depende do ambiente
    import yaml as _yaml
except ImportError:  # pragma: no cover
    _yaml = None

#: Chave ancorada numa indentação. `re.M` + âncora garante o nível certo:
#: `  name:` indentado é campo de outro objeto, não título do doc — exceto
#: quando é filho direto de `info:` (ver `_from_info`).
_KEY = r"^{indent}{key}:[ \t]*(?:\"|')?(.+?)(?:\"|')?[ \t]*$"


@cache
def _key_re(key: str, indent: str = "") -> "re.Pattern[str]":
    return re.compile(_KEY.format(indent=re.escape(indent), key=key),
                      re.MULTILINE)


_NAME_RE = _key_re("name")
_ID_RE = _key_re("id")
_DESC_RE = _key_re("description")

#: Bloco `info:` com suas linhas indentadas (OpenAPI, agent cards…).
_INFO_RE = re.compile(r"^info:[ \t]*\n((?:[ \t]+.*\n?)+)", re.MULTILINE)


def _from_regex(text: str, pattern: "re.Pattern[str]") -> "str | None":
    match = pattern.search(text)
    return match.group(1) if match else None


def _from_info(text: str, key: str) -> "str | None":
    """`info.<key>` — só filho DIRETO, igual a `doc["info"][key]` no PyYAML.

    Sem isto o caminho regex não enxerga `info.name`, e o título de um doc
    OpenAPI passa a depender de PyYAML estar instalado.
    """
    block = _INFO_RE.search(text)
    if not block:
        return None
    body = block.group(1)
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return None
    indent = min((line[:len(line) - len(line.lstrip())] for line in lines),
                 key=len)
    return _from_regex(body, _key_re(key, indent))


def _first_str(*values: object) -> "str | None":
    """Primeiro valor que é string não-vazia (YAML devolve int/None/dict)."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None


def _from_yaml(text: str) -> "tuple[str | None, str | None]":
    """(title, description) via PyYAML, ou (None, None) se não der."""
    if _yaml is None:  # pragma: no cover - depende do ambiente
        return None, None
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
    title, desc = _from_yaml(text)
    if title is None:
        title = _first_str(_from_regex(text, _NAME_RE),
                           _from_regex(text, _ID_RE),
                           _from_info(text, "name"))
    if desc is None:
        desc = _first_str(_from_regex(text, _DESC_RE),
                          _from_info(text, "description"))
    return Scanned(
        name=oneline(title or path.stem, 120) or path.stem,
        description=oneline(desc or text),
        body=text,
        source_path=str(path),
        fmt="yaml",
    )
