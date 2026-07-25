"""tests/test_archive.py"""
import hashlib
import zlib

import pytest

from neurata.archive import (
    ArchiveCorruptError,
    ArchiveMissingError,
    BadShaError,
    get,
    has,
    put,
)
from neurata.home import NeurataHome


def _home(tmp_path):
    home = NeurataHome(tmp_path)
    home.init()
    return home


def test_put_get_roundtrip(tmp_path):
    home = _home(tmp_path)
    data = "Conteúdo original — bytes exatos.\n".encode()
    sha = put(home, data)
    assert sha == hashlib.sha256(data).hexdigest()
    assert get(home, sha) == data


def test_layout_git_objects(tmp_path):
    home = _home(tmp_path)
    sha = put(home, b"x")
    path = home.archive / sha[:2] / sha[2:]
    assert path.exists()
    # disco guarda comprimido, não plaintext
    assert zlib.decompress(path.read_bytes()) == b"x"


def test_put_idempotente_dedup(tmp_path):
    home = _home(tmp_path)
    sha1 = put(home, b"mesmo conteudo")
    mtime = (home.archive / sha1[:2] / sha1[2:]).stat().st_mtime_ns
    sha2 = put(home, b"mesmo conteudo")
    assert sha1 == sha2
    # segundo put é no-op: arquivo não reescrito
    assert (home.archive / sha1[:2] / sha1[2:]).stat().st_mtime_ns == mtime


def test_get_missing(tmp_path):
    home = _home(tmp_path)
    with pytest.raises(ArchiveMissingError):
        get(home, "0" * 64)


def test_get_corrupt_detected(tmp_path):
    home = _home(tmp_path)
    sha = put(home, b"integro")
    path = home.archive / sha[:2] / sha[2:]
    path.write_bytes(zlib.compress(b"trocado por baixo"))
    with pytest.raises(ArchiveCorruptError) as exc:
        get(home, sha)
    assert "remedi" in str(exc.value).lower() or "reindex" in str(exc.value)


def test_get_undecompressable(tmp_path):
    home = _home(tmp_path)
    sha = put(home, b"ok")
    (home.archive / sha[:2] / sha[2:]).write_bytes(b"\x00nao-e-zlib")
    with pytest.raises(ArchiveCorruptError):
        get(home, sha)


@pytest.mark.parametrize("bad", [
    "", "abc", "Z" * 64, "../../../../etc/passwd", "0" * 63, "0" * 65,
    "0" * 62 + "/x",
])
def test_sha_validado_nunca_monta_path(tmp_path, bad):
    home = _home(tmp_path)
    with pytest.raises(BadShaError):
        get(home, bad)
    with pytest.raises(BadShaError):
        has(home, bad)


def test_has(tmp_path):
    home = _home(tmp_path)
    sha = put(home, b"presente")
    assert has(home, sha)
    assert not has(home, "f" * 64)


def test_sem_tmp_sobrando(tmp_path):
    home = _home(tmp_path)
    put(home, b"a")
    put(home, b"b")
    leftovers = [p for p in home.archive.rglob("*") if p.name.startswith(".tmp")]
    assert leftovers == []
