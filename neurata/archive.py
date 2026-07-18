"""neurata/archive.py — frio content-addressed, estilo git-objects.

sha256 do conteúdo ORIGINAL (não do comprimido); zlib no disco; write
atômico tmp+rename no MESMO diretório; dedup grátis pelo hash. Delete não
existe — nem função privada. Sha validado na entrada: nunca monta path com
input cru (guard herdado da fase 1).
"""
import hashlib
import os
import re
import zlib
from pathlib import Path

from neurata.home import NeurataHome

_SHA = re.compile(r"^[0-9a-f]{64}$")


class BadShaError(ValueError):
    pass


class ArchiveMissingError(KeyError):
    pass


class ArchiveCorruptError(RuntimeError):
    pass


def put(home: NeurataHome, data: bytes) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = _path(home, sha)
    if path.exists():  # dedup: content-addressed, já está lá
        return sha
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{os.getpid()}-{sha[:8]}"
    try:
        tmp.write_bytes(zlib.compress(data))
        os.replace(tmp, path)  # atômico no mesmo filesystem
    finally:
        tmp.unlink(missing_ok=True)
    return sha


def get(home: NeurataHome, sha: str) -> bytes:
    path = _path(home, sha)
    if not path.exists():
        raise ArchiveMissingError(
            f"blob {sha} ausente do archive — remediação: o full original "
            "não está mais no frio; verifique backups do diretório archive/")
    try:
        data = zlib.decompress(path.read_bytes())
    except zlib.error as exc:
        raise ArchiveCorruptError(
            f"blob {sha} ilegível ({exc}) — remediação: restaure "
            f"{path} de backup; o archive nunca é reescrito pelo Neurata"
        ) from exc
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha:
        raise ArchiveCorruptError(
            f"blob {sha} corrompido (hash real {actual}) — remediação: "
            f"restaure {path} de backup; conteúdo não bate com o endereço")
    return data


def has(home: NeurataHome, sha: str) -> bool:
    return _path(home, sha).exists()


def _path(home: NeurataHome, sha: str) -> Path:
    if not isinstance(sha, str) or not _SHA.match(sha):
        raise BadShaError(
            f"sha inválido: {sha!r} (esperado 64 hex minúsculos)")
    return home.archive / sha[:2] / sha[2:]
