"""neurata/frontmatter.py — YAML restrito (subset válido p/ Obsidian).

Suporta: `k: v` (str), `k: [a, b]` (lista de str), `k:` + bloco indentado
2 espaços (dict 1 nível). Valores com caracteres especiais são citados.
Nada além disso — arquivos à mão fora do subset dão FrontmatterError, e
`serialize` recusa estruturas fora do subset (dict profundo, lista dentro
de dict, dict dentro de lista, objetos arbitrários). Escalares int/bool/
float/None são coeridos p/ str na escrita (design: tudo vira str).
"""


class FrontmatterError(ValueError):
    pass


def parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 3)
    if end == -1:
        raise FrontmatterError("frontmatter sem terminador '---'")
    block, body = text[4:end], text[end + 5:]
    meta: dict = {}
    nested: str | None = None
    for raw in block.splitlines():
        if not raw.strip():
            continue
        if raw.startswith("  ") and nested is not None:
            k, v = _kv(raw)
            meta[nested][k] = _scalar(v)
            continue
        nested = None
        k, v = _kv(raw)
        if v == "":
            meta[k] = {}
            nested = k
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            meta[k] = _split_list(inner) if inner else []
        else:
            meta[k] = _scalar(v)
    return meta, body


def serialize(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sk, sv in v.items():
                _check_scalar(sv, f"{k}.{sk}")
                lines.append(f"  {sk}: {_emit(sv)}")
        elif isinstance(v, list):
            for i, item in enumerate(v):
                _check_scalar(item, f"{k}[{i}]")
            lines.append(f"{k}: [{', '.join(_emit(i) for i in v)}]")
        else:
            _check_scalar(v, k)
            lines.append(f"{k}: {_emit(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _kv(raw: str) -> tuple[str, str]:
    stripped = raw.strip()
    if ":" not in stripped:
        raise FrontmatterError(f"linha inválida no frontmatter: {raw!r}")
    k, _, v = stripped.partition(":")
    return k.strip(), v.strip()


def _split_list(inner: str) -> list[str]:
    """Divide lista inline em vírgulas FORA de aspas; desfaz aspas por item."""
    items: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    while i < len(inner):
        c = inner[i]
        if quote is not None:
            buf.append(c)
            if c == "\\" and quote == '"' and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
            buf.append(c)
        elif c == ",":
            items.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if quote is not None:
        raise FrontmatterError(f"aspas sem fechamento em lista inline: [{inner}]")
    items.append("".join(buf))
    return [_scalar(p.strip()) for p in items]


_SCALAR_TYPES = (str, int, float, bool, type(None))


def _check_scalar(v: object, where: str) -> None:
    if not isinstance(v, _SCALAR_TYPES):
        raise FrontmatterError(
            f"valor fora do subset em {where!r}: {type(v).__name__} "
            "(permitido: str escalar, lista de str, dict 1 nível de str)"
        )


def _scalar(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"')
    return v


def _emit(v: object) -> str:
    s = str(v)
    if s == "" or s != s.strip() or any(c in s for c in ':#[]{},"\''):
        return '"' + s.replace('"', '\\"') + '"'
    return s
