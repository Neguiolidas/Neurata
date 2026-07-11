"""armarium/ulid.py — ULID stdlib-only (spec Crockford base32)."""
import os
import time

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ts_ms: int | None = None) -> str:
    if ts_ms is None:
        ts_ms = time.time_ns() // 1_000_000
    value = (ts_ms << 80) | int.from_bytes(os.urandom(10), "big")
    chars = []
    for _ in range(26):
        chars.append(_B32[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))
