"""Stable identifiers (docs/CONTRACTS.md §3). LOCKED.

Every stream builds IDs with these helpers, never with its own hashing.
"""

from __future__ import annotations

import hashlib

_SEP = "\x1f"
_PREFIX = "sha256:"


def make_id(*parts: str) -> str:
    """Return a stable ID: ``"sha256:" + hex(sha256("\\x1f".join(parts)))``.

    Inputs: any number of strings (order matters). Same inputs always give the same ID.
    Raises TypeError if a part is not a str, so callers cannot silently hash ``None`` or ints.
    """
    for part in parts:
        if not isinstance(part, str):
            raise TypeError(f"make_id parts must be str, got {type(part).__name__}")
    return _PREFIX + hashlib.sha256(_SEP.join(parts).encode("utf-8")).hexdigest()


def hash_bytes(data: bytes) -> str:
    """Return ``"sha256:" + hex(sha256(data))`` for raw bytes (e.g. an evidence snippet span)."""
    return _PREFIX + hashlib.sha256(data).hexdigest()
