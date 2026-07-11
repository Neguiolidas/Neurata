"""armarium/textnorm.py — normalização PT-aware p/ index e slugs."""
import re
import unicodedata

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NONALNUM = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def split_identifiers(text: str) -> str:
    text = text.replace("_", " ").replace("-", " ")
    return _CAMEL.sub(" ", text)


def normalize(text: str) -> str:
    out = strip_accents(split_identifiers(text)).lower()
    out = _NONALNUM.sub(" ", out)
    return _WS.sub(" ", out).strip()


def slugify(title: str, max_len: int = 60) -> str:
    base = strip_accents(title).lower()
    base = _NONALNUM.sub("-", base).strip("-")
    return base[:max_len].rstrip("-") or "untitled"
