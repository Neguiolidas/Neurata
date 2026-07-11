"""armarium/deposit.py — captura crua → inbox. Nunca bloqueia por qualidade."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from armarium.envelope import capture
from armarium.frontmatter import serialize
from armarium.home import ArmariumHome
from armarium.textnorm import slugify
from armarium.ulid import new_ulid

MAX_BYTES = 2_000_000


class DepositError(ValueError):
    pass


def deposit(home: ArmariumHome, content: "str | None" = None,
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
    entry_title = title or _first_line(content) or "sem título"
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


def _find_previous(home: ArmariumHome, content_hash: str) -> "dict | None":
    for rec in home.read_log("deposits"):
        if rec.get("hash") == content_hash and rec.get("action") == "created":
            return rec
    return None


def _first_line(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""
