"""Turn the Ada extractor's NormalizedCodeModel into the shared graph format (extractor/common/schema.py).

    python -m extractor.ada.convert data/adl.ncm.json --out data/ADL.graph.json

Only facts the extractor actually found are carried over. Calls it could not resolve stay as
`indirect_calls` or `may_call` edges, never promoted to plain calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from extractor.common.schema import Edge, FieldNode, FileNode, FunctionNode, Graph, save_graph

_EDGE_KINDS = {"CALLS": "calls", "MAY_CALL": "may_call", "READS": "reads", "WRITES": "writes"}


def _container(props: dict) -> str | None:
    c = props.get("container")
    return None if c in (None, "", "?") else c


def _field_type(signature: str | None) -> str:
    """'HSE_VALUE : constant UInt32' -> 'UInt32'."""
    if not signature or ":" not in signature:
        return ""
    type_part = signature.split(":", 1)[1].split(":=", 1)[0].strip()
    words = [w for w in type_part.split() if w not in {"constant", "aliased"}]
    return " ".join(words)


def ncm_to_graph(model: dict, repo_name: str) -> Graph:
    manifest = model.get("manifest", {})
    nodes = model.get("nodes", [])
    kind_of = {n["id"]: n["kind"] for n in nodes}

    graph_nodes: list[FunctionNode | FieldNode | FileNode] = []
    fns: dict[str, FunctionNode] = {}
    file_ids: dict[str, str] = {}  # repo-relative path -> file node id
    for n in nodes:
        k, props = n["kind"], n.get("properties", {})
        if k == "File":
            fn = FileNode(id=n["id"], path=n["file"], language="ada")
            file_ids[n["file"]] = n["id"]
            graph_nodes.append(fn)
        elif k == "Function":
            f = FunctionNode(
                id=n["id"],
                name=n["name"],
                qualified_name=n["qualified_name"],
                container=_container(props),
                language="ada",
                file=n["file"],
                line_start=n["start_line"],
                line_end=n["end_line"],
                signature=n.get("signature") or n["name"],
                is_virtual=bool(props.get("abstract")),
                is_pure_virtual=props.get("has_body") is False,
                loc=max(n["end_line"] - n["start_line"] + 1, 0),
            )
            fns[f.id] = f
            graph_nodes.append(f)
        elif k == "Variable":
            graph_nodes.append(
                FieldNode(
                    id=n["id"],
                    name=n["name"],
                    qualified_name=n["qualified_name"],
                    container=n["qualified_name"].rsplit(".", 1)[0] if "." in n["qualified_name"] else None,
                    type=_field_type(n.get("signature")),
                    file=n["file"],
                    line=n["start_line"],
                )
            )
        # Module and ExternalSymbol nodes are not part of the shared format.

    kept = {n.id for n in graph_nodes}
    grouped: dict[tuple[str, str, str], Edge] = {}

    def add(source: str, target: str, kind: str, line: int | None, dispatch: bool = False) -> None:
        e = grouped.get((source, target, kind))
        if e is None:
            e = grouped[(source, target, kind)] = Edge(source=source, target=target, kind=kind, count=0, dispatch=dispatch)
        e.count += 1
        if line is not None and line not in e.lines:
            e.lines.append(line)
        e.dispatch = e.dispatch or dispatch

    for e in model.get("edges", []):
        t, src, dst, props = e["type"], e["source"], e["target"], e.get("properties", {})
        line = props.get("call_site_line")
        if t in _EDGE_KINDS:
            if src in fns and dst in kept:
                dispatch = t == "CALLS" and bool(props.get("dispatching"))
                add(src, dst, _EDGE_KINDS[t], line, dispatch)
            elif t == "CALLS" and src in fns and kind_of.get(dst) == "ExternalSymbol":
                fns[src].external_calls += 1
        elif t == "UNRESOLVED_CALL" and src in fns:
            if line is not None:
                fns[src].indirect_calls.append(line)
        elif t == "DEFINES" and src in kept and dst in kept:
            add(dst, src, "defined_in", None)

    for n in graph_nodes:
        if isinstance(n, FieldNode) and n.file in file_ids:
            add(n.id, file_ids[n.file], "defined_in", None)

    for f in fns.values():
        f.recursive = (f.id, f.id, "calls") in grouped
    for f in fns.values():
        f.indirect_calls.sort()

    created = manifest.get("created_at")
    graph = Graph(
        repo_name=repo_name,
        commit_sha=manifest.get("commit"),
        language="ada",
        extracted_at=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else datetime.now(UTC),
        nodes=graph_nodes,
        edges=list(grouped.values()),
    )
    graph.refresh_stats()
    return graph


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m extractor.ada.convert", description=__doc__)
    ap.add_argument("ncm", type=Path, help="NormalizedCodeModel JSON from the Ada extractor")
    ap.add_argument("--out", type=Path, required=True, help="where to write the graph JSON")
    ap.add_argument("--name", help="repo name (default: the repo folder named in the model)")
    args = ap.parse_args(argv)

    model = json.loads(args.ncm.read_text(encoding="utf-8"))
    name = args.name or Path(model.get("manifest", {}).get("repo_path", "")).name or args.ncm.stem
    graph = ncm_to_graph(model, name)
    save_graph(graph, args.out)
    print(f"wrote {args.out}: {graph.stats.nodes}, {graph.stats.edges}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
