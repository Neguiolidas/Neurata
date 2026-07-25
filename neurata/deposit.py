"""neurata/deposit.py — captura crua → inbox. Nunca bloqueia por qualidade."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from neurata.envelope import capture
from neurata.frontmatter import serialize
from neurata.home import NeurataHome
from neurata.textnorm import slugify
from neurata.ulid import new_ulid

MAX_BYTES = 2_000_000


class DepositError(ValueError):
    pass


def deposit(home: NeurataHome, content: "str | None" = None,
            file: "Path | None" = None, *, title: "str | None" = None,
            dtype: str = "note", denv: str = "generic",
            agent: "str | None" = None,
            session: "str | None" = None) -> dict:
    if (content is None) == (file is None):
        raise DepositError("passe exatamente um: content OU file")
    origin = "manual"
    if file is not None:
        file = Path(file)
        if not file.is_file():
            raise DepositError(f"arquivo não encontrado: {file}")
        if file.stat().st_size > MAX_BYTES:
            raise DepositError(f"depósito excede o cap de {MAX_BYTES} bytes")
        content = file.read_text(encoding="utf-8", errors="replace")
        origin = str(file)
    assert content is not None
    if len(content.encode("utf-8")) > MAX_BYTES:
        raise DepositError(f"depósito excede o cap de {MAX_BYTES} bytes")
    if not content.strip():
        raise DepositError("depósito vazio: conteúdo sem texto")

    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    envelope = capture(origin=origin, agent=agent, session=session)
    previous = _find_previous(home, content_hash)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if previous is not None:
        event = {"ts": now, "hash": content_hash, "action": "duplicate",
                 "id": previous["id"], "path": previous["path"],
                 "envelope": envelope}
        home.append_log("deposits", event)
        return {"action": "duplicate", "id": previous["id"],
                "path": previous["path"], "hash": content_hash}

    entry_id = new_ulid()
    entry_title = _sanitize_title(title or _first_line(content)) or "sem título"
    slug = slugify(entry_title)
    path = home.inbox / f"{entry_id}-{slug}.md"
    meta = {
        "id": entry_id,
        "type": dtype,
        "env": denv,
        "title": entry_title,
        "description": _first_line(content)[:140],
        "source": _flatten(envelope),
        "created": now,
        "content_hash": content_hash,
    }
    path.write_text(serialize(meta, content), encoding="utf-8")
    rel = str(path.relative_to(home.root))
    # Write-then-log é não atômico: um crash entre as duas linhas deixa um
    # arquivo órfão (sem evento "created" no log). A verificação de
    # existência em _find_previous (FIX 1) cobre a direção inversa —
    # log aponta p/ arquivo apagado — permitindo redepósito seguro; o
    # órfão em si fica para uma reconciliação futura (ex.: reindex/gc).
    home.append_log("deposits", {"ts": now, "hash": content_hash,
                                 "action": "created", "id": entry_id,
                                 "path": rel, "envelope": envelope})
    return {"action": "created", "id": entry_id, "path": rel,
            "hash": content_hash}


def _flatten(envelope: dict) -> dict:
    """Achata dicts aninhados do envelope p/ o subset do frontmatter.

    `git: {root, commit, branch}` vira `git_root`/`git_commit`/`git_branch`.
    O envelope COMPLETO (aninhado) segue intacto p/ logs/deposits.jsonl.
    """
    flat: dict = {}
    for k, v in envelope.items():
        if isinstance(v, dict):
            for sk, sv in v.items():
                flat[f"{k}_{sk}"] = sv
        else:
            flat[k] = v
    return flat


def _find_previous(home: NeurataHome, content_hash: str) -> "dict | None":
    for rec in home.read_log("deposits"):
        if rec.get("hash") != content_hash or rec.get("action") != "created":
            continue
        # Um "created" no log só conta como duplicata viva se o arquivo
        # que ele aponta ainda existe. `path` é gravado relativo a
        # home.root (ver `rel = str(path.relative_to(home.root))` acima),
        # então resolvemos do mesmo jeito para checar existência.
        recorded_path = rec.get("path")
        if not recorded_path or not (home.root / recorded_path).is_file():
            continue
        return rec
    return None


def _sanitize_title(title: "str | None") -> "str | None":
    if title is None:
        return None
    # Colapsa newline/CR (e runs de whitespace) p/ 1 espaço — um título
    # multilinha gera frontmatter que `frontmatter.parse` não reparseia.
    return " ".join(title.split())


def _first_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
