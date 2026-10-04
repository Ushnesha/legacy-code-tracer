"""Splitting qualified names: C++ writes `Ns::Class::method`, Ada writes `Pkg.Sub.Proc`.

C++ names never contain a dot and Ada names never contain `::`, so the separator is read from
the name itself. C++ behaviour is exactly the old `::` handling.
"""

from __future__ import annotations


def _sep(qualified: str) -> str | None:
    if "::" in qualified:
        return "::"
    if "." in qualified:
        return "."
    return None


def container_of(qualified: str) -> str | None:
    """'A::B::f' -> 'A::B', 'Pkg.Proc' -> 'Pkg', 'f' -> None."""
    sep = _sep(qualified)
    return qualified.rsplit(sep, 1)[0] if sep else None


def last_part(qualified: str) -> str:
    sep = _sep(qualified)
    return qualified.rsplit(sep, 1)[1] if sep else qualified


def ends_with_name(qualified: str, name: str) -> bool:
    """True if `qualified` is `name` or ends with `<sep>name`."""
    return qualified == name or qualified.endswith("::" + name) or qualified.endswith("." + name)


def member_patterns(container: str) -> tuple[str, str]:
    """SQL LIKE patterns for names directly or deeply under `container` (callers still check exactly)."""
    return container + "::%", container + ".%"
