"""armarium/frontmatter.py — YAML restrito (subset válido p/ Obsidian).

Suporta: `k: v` (str), `k: [a, b]` (lista de str), `k:` + bloco indentado
2 espaços (dict 1 nível). Valores com caracteres especiais são citados.
Nada além disso — arquivos à mão fora do subset dão FrontmatterError.
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
            meta[k] = [_scalar(p.strip()) for p in inner.split(",")] if inner else []
        else:
            meta[k] = _scalar(v)
    return meta, body


def serialize(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sk, sv in v.items():
                lines.append(f"  {sk}: {_emit(sv)}")
        elif isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_emit(i) for i in v)}]")
        else:
            lines.append(f"{k}: {_emit(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _kv(raw: str) -> tuple[str, str]:
    stripped = raw.strip()
    if ":" not in stripped:
        raise FrontmatterError(f"linha inválida no frontmatter: {raw!r}")
    k, _, v = stripped.partition(":")
    return k.strip(), v.strip()


def _scalar(v: str) -> str:
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1].replace('\\"', '"')
    return v


def _emit(v: object) -> str:
    s = str(v)
    if s == "" or s != s.strip() or any(c in s for c in ':#[]{},"\''):
        return '"' + s.replace('"', '\\"') + '"'
    return s
