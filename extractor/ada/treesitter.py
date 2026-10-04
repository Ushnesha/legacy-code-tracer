"""Fallback Ada backend: tree-sitter-ada, subprograms and name-based calls only.

Nothing here resolves names semantically, so every call edge carries ``resolved=False`` and
FALLBACK_PARSED evidence. No reads/writes are emitted in this mode.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from .builder import ModelBuilder

SUBPROGRAM_NODES = {"subprogram_body", "expression_function_declaration", "entry_body"}
CONTAINER_NODES = {"package_body", "protected_body", "task_body", "package_declaration"}
BEST_GUESS = "Ada links are best-guess in this mode."
MAX_FAILED_FRACTION = 0.05


def available() -> bool:
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_ada  # noqa: F401
    except ImportError:
        return False
    return True


def _pt(node, which: str) -> tuple[int, int]:
    """1-based (line, column) of a tree-sitter node's start/end."""
    row, col = node.start_point if which == "start" else node.end_point
    return row + 1, col + 1


def _text(node) -> str:
    return node.text.decode("utf-8", errors="replace")


def _simple_name(node) -> str | None:
    """Last identifier of an identifier / selected_component, lowercased for matching."""
    if node is None:
        return None
    if node.type == "identifier":
        return _text(node).lower()
    if node.type == "selected_component":
        ids = [c for c in node.children if c.type == "identifier"]
        return _text(ids[-1]).lower() if ids else None
    return None


def _spec_of(node):
    for child in node.children:
        if child.type in {"procedure_specification", "function_specification", "entry_declaration"}:
            return child
    return None


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def extract(builder: ModelBuilder, files: list[Path]) -> tuple[int, int]:
    """Parse `files` into `builder`; returns (files_with_errors, total_files)."""
    import tree_sitter
    import tree_sitter_ada

    parser = tree_sitter.Parser(tree_sitter.Language(tree_sitter_ada.language()))
    functions: list[tuple[str, object, str, str]] = []  # (node_id, ts_node, file, container)
    by_name: dict[str, list[str]] = defaultdict(list)
    failed = 0
    task_bodies: list[tuple[str, object, str, str]] = []

    for path in files:
        src = builder.source(path)
        tree = parser.parse(src.data)
        if tree.root_node.has_error:
            failed += 1
            builder.set_coverage(path, "FAILED", "tree-sitter reported syntax errors")
            continue
        builder.set_coverage(path, "FALLBACK_PARSED", BEST_GUESS)
        file_id = builder.file_node(path)

        def visit(
            node, scope: list[str], container_id: str | None, path=path, file_id=file_id
        ) -> None:
            for child in node.children:
                if child.type in CONTAINER_NODES or child.type == "task_body":
                    name_node = next((c for c in child.children if c.type == "identifier"), None)
                    name = _text(name_node) if name_node is not None else "?"
                    visit(child, [*scope, name], container_id, path, file_id)
                elif child.type in SUBPROGRAM_NODES:
                    spec = _spec_of(child)
                    name_node = next(
                        (c for c in (spec.children if spec else []) if c.type == "identifier"), None
                    )
                    if name_node is None:
                        continue
                    name = _text(name_node)
                    qname = ".".join([*scope, name])
                    sig = re.sub(r"\s+", " ", _text(spec)).strip()
                    fid = builder.add_node(
                        "Function",
                        name,
                        qname,
                        path,
                        (_pt(child, "start"), _pt(child, "end")),
                        signature=sig,
                        properties={"container": ".".join(scope) or None, "resolved": False},
                    )
                    builder.add_edge(
                        file_id,
                        fid,
                        "DEFINES",
                        builder.nodes[fid].evidence_ids[0],
                        {"resolved": False},
                    )
                    functions.append((fid, child, str(path), qname))
                    by_name[name.lower()].append(fid)
                    visit(child, [*scope, name], container_id, path, file_id)
                else:
                    visit(child, scope, container_id, path, file_id)

        visit(tree.root_node, [], None, path, file_id)

        # Task bodies are entry points (name-based, nothing to resolve).
        for node in _walk(tree.root_node):
            if node.type == "task_body":
                name_node = next((c for c in node.children if c.type == "identifier"), None)
                if name_node is not None:
                    tid = builder.add_node(
                        "Function",
                        _text(name_node),
                        _text(name_node),
                        path,
                        (_pt(node, "start"), _pt(node, "end")),
                        signature="task body",
                        properties={"entry_point": True, "resolved": False},
                    )
                    task_bodies.append((tid, node, str(path), _text(name_node)))

    # Calls: name-based only.
    for fid, node, path, _qname in [*functions, *task_bodies]:
        order = 0
        for sub in _walk(node):
            if sub.type == "procedure_call_statement" or sub.type == "function_call":
                target = next(
                    (c for c in sub.children if c.type in {"identifier", "selected_component"}),
                    None,
                )
            else:
                continue
            name = _simple_name(target)
            candidates = by_name.get(name or "", [])
            if not candidates:
                continue
            order += 1
            ev = builder.add_evidence(path, _pt(sub, "start"), _pt(sub, "end"))
            etype = "CALLS" if len(candidates) == 1 else "MAY_CALL"
            for cid in candidates:
                builder.add_edge(
                    fid,
                    cid,
                    etype,
                    ev,
                    {
                        "call_site_line": _pt(sub, "start")[0],
                        "order": order,
                        "resolved": False,
                        "via": name,
                    },
                    line=_pt(sub, "start")[0] * 1000 + order,
                )

    builder.known_gaps.append(BEST_GUESS)
    builder.known_gaps.append(
        "tree-sitter mode: calls are matched by name only; reads/writes not extracted."
    )
    return failed, len(files)
