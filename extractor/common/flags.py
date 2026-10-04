"""Places where the graph cannot give a complete answer about a function, for an engineer to look at.

Worked out from graph facts alone, never by the model, and never guessed: every flag points at something the
extractor recorded (calls through a pointer, calls it could not resolve, virtual dispatch, ...).
Severity is "review" (a person should look before relying on the explanation) or "note" (good to know).
"""

from __future__ import annotations

from typing import Any, Mapping, TypedDict

MANY_EXTERNAL_CALLS = 10  # this many calls outside the repo make the behaviour depend heavily on other code


class Flag(TypedDict):
    code: str
    severity: str  # "review" | "note"
    message: str  # plain English
    lines: list[int]  # source lines it refers to (may be empty)


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def function_flags(node: Mapping[str, Any], may_call_targets: int = 0) -> list[Flag]:
    """Flags for one function. `node` is its graph node (as stored), `may_call_targets` the number of
    possible targets it reaches through virtual calls."""
    flags: list[Flag] = []

    if node.get("is_pure_virtual"):
        flags.append({
            "code": "no_body", "severity": "note", "lines": [],
            "message": "This has no body here. It only says what subclasses must provide.",
        })

    indirect = sorted(node.get("indirect_calls") or [])
    if indirect:
        flags.append({
            "code": "indirect_calls", "severity": "review", "lines": indirect,
            "message": f"It makes {_plural(len(indirect), 'call', 'calls')} through a pointer. "
                       "We cannot tell what gets called, so the call tree may be missing steps.",
        })

    unresolved = int(node.get("unresolved_calls") or 0)
    if unresolved:
        flags.append({
            "code": "unresolved_calls", "severity": "review", "lines": [],
            "message": f"{_plural(unresolved, 'call goes', 'calls go')} to code in this repo whose definition we did "
                       "not find. They are left out of the call tree rather than guessed.",
        })

    if may_call_targets:
        flags.append({
            "code": "virtual_dispatch", "severity": "note", "lines": [],
            "message": f"Some calls are virtual, so {_plural(may_call_targets, 'different function', 'different functions')} "
                       "could run depending on the object. Which one runs is decided while the program is running.",
        })

    external = int(node.get("external_calls") or 0)
    if external >= MANY_EXTERNAL_CALLS:
        flags.append({
            "code": "many_external_calls", "severity": "note", "lines": [],
            "message": f"It makes {external} calls to code outside this repo (standard library or other libraries), "
                       "which we do not read.",
        })

    if node.get("recursive"):
        flags.append({
            "code": "recursive", "severity": "note", "lines": [],
            "message": "It calls itself, so how long it runs depends on its input.",
        })

    return flags
