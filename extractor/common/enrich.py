"""Enrich pass: runs after any language extractor and edits the Graph in place.

    python -m extractor.common.enrich data/<name>.graph.json

What it adds (nothing is ever deleted; everything it creates is marked `inferred` so a re-run
starts clean):
  1. overrides edges from the class hierarchy, and may_call edges for virtual dispatch
  2. noise tags (is_noise + noise_reason)
  3. entry points (is_entry_point + the ranked graph.entry_points list)
  4. metrics on every function: fan_in, fan_out, reach, fields_written_count
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from collections import defaultdict
from pathlib import Path

from .config import PROJECT_ROOT, RepoConfig, find_config
from .schema import Edge, FunctionNode, Graph, load_graph, save_graph

CALL_KINDS = ("calls", "may_call")
MIN_CALLS_FOR_ENTRY_POINT = 3
ACCESSOR_MAX_LOC = 4


# ---------------------------------------------------------------- helpers


def class_of(fn: FunctionNode) -> str | None:
    """Qualified class name of a method (None for free functions)."""
    if fn.container is None or "::" not in fn.qualified_name:
        return None
    return fn.qualified_name.rsplit("::", 1)[0]


def method_key(fn: FunctionNode) -> tuple | None:
    if fn.param_types is None:
        return None
    return (fn.name, tuple(fn.param_types), fn.signature.endswith(" const"))


def is_ctor_or_dtor(fn: FunctionNode) -> bool:
    return fn.container is not None and (fn.name == fn.container or fn.name.startswith("~"))


def _closure(start: str, children: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    todo = list(children.get(start, ()))
    while todo:
        x = todo.pop()
        if x not in seen:
            seen.add(x)
            todo.extend(children.get(x, ()))
    return seen


# ---------------------------------------------------------------- 1. hierarchy


def add_override_edges(graph: Graph, fns: list[FunctionNode]) -> int:
    """derived method -> nearest base method with the same name and parameter types."""
    by_class: dict[str, dict[tuple, FunctionNode]] = defaultdict(dict)
    for f in fns:
        cls, key = class_of(f), method_key(f)
        if cls and key and not is_ctor_or_dtor(f):
            by_class[cls].setdefault(key, f)

    have = {(e.source, e.target) for e in graph.edges if e.kind == "overrides"}
    new_edges: list[Edge] = []
    for f in fns:
        cls = class_of(f)
        key = method_key(f)
        # destructors have a different name in every class; the extractor already links those
        if not cls or key is None or is_ctor_or_dtor(f):
            continue
        # walk up each inheritance path; stop at the first class that has a matching virtual method
        seen: set[str] = set()
        todo = list(graph.class_bases.get(cls, []))
        while todo:
            base = todo.pop(0)
            if base in seen:
                continue
            seen.add(base)
            hit = by_class.get(base, {}).get(key)
            if hit is not None and hit.is_virtual and hit.id != f.id:
                if (f.id, hit.id) not in have:
                    have.add((f.id, hit.id))
                    new_edges.append(Edge(source=f.id, target=hit.id, kind="overrides", count=1, inferred=True))
            else:
                todo.extend(graph.class_bases.get(base, []))
    graph.edges.extend(new_edges)
    return len(new_edges)


def add_may_call_edges(graph: Graph, by_id: dict[str, FunctionNode]) -> int:
    """For each virtual call through a pointer/reference, add may_call to every override."""
    overridden_by: dict[str, list[str]] = defaultdict(list)
    for e in graph.edges:
        if e.kind == "overrides":
            overridden_by[e.target].append(e.source)

    existing = {(e.source, e.target): e for e in graph.edges if e.kind == "may_call"}
    created: dict[tuple[str, str], Edge] = {}
    for e in list(graph.edges):
        if e.kind != "calls" or not e.dispatch:
            continue
        target = by_id.get(e.target)
        if target is None or not target.is_virtual:
            continue
        for o in sorted(_closure(e.target, overridden_by)):
            if o == e.source:
                continue
            key = (e.source, o)
            if key in existing:
                continue
            mc = created.get(key)
            if mc is None:
                mc = created[key] = Edge(source=e.source, target=o, kind="may_call", count=0, inferred=True)
            mc.lines = sorted(set(mc.lines) | set(e.lines))
            mc.count = len(mc.lines) or 1
    graph.edges.extend(created.values())
    return len(created)


# ---------------------------------------------------------------- 2. noise


def tag_noise(fns: list[FunctionNode], graph: Graph, cfg: RepoConfig) -> None:
    has_edge_calls: set[str] = set()
    fields_touched: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.kind == "calls" and e.source != e.target or (e.kind == "may_call"):
            has_edge_calls.add(e.source)
        elif e.kind in ("reads", "writes"):
            fields_touched[e.source].add(e.target)

    for f in fns:
        calls_something = f.id in has_edge_calls or f.external_calls > 0 or bool(f.indirect_calls)
        cls = class_of(f)
        reason = None
        if f.name.startswith("operator"):
            reason = "operator"
        elif cls and any(cls == c or cls.endswith("::" + c) for c in cfg.noise_classes):
            reason = "noise_class"
        elif any(fnmatch.fnmatch(f.name, p) or fnmatch.fnmatch(f.qualified_name, p) for p in cfg.noise_name_patterns):
            reason = "name_pattern"
        elif is_ctor_or_dtor(f) and not calls_something:
            reason = "trivial_constructor" if not f.name.startswith("~") else "trivial_destructor"
        elif (
            f.loc <= ACCESSOR_MAX_LOC
            and not f.is_pure_virtual
            and not calls_something
            and len(fields_touched[f.id]) == 1
        ):
            reason = "accessor"
        f.is_noise = reason is not None
        f.noise_reason = reason


# ---------------------------------------------------------------- 3 + 4. metrics, entry points


def _sccs(n: int, succ: list[set[int]]) -> list[list[int]]:
    """Iterative Tarjan. Components come out sinks-first (reverse topological order)."""
    index = [-1] * n
    low = [0] * n
    on = [False] * n
    stack: list[int] = []
    out: list[list[int]] = []
    counter = 0
    for root in range(n):
        if index[root] != -1:
            continue
        work = [(root, iter(succ[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on[root] = True
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if index[w] == -1:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on[w] = True
                    work.append((w, iter(succ[w])))
                    advanced = True
                    break
                if on[w]:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on[w] = False
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def compute_metrics(graph: Graph, fns: list[FunctionNode]) -> dict[str, set[str]]:
    idx = {f.id: i for i, f in enumerate(fns)}
    succ: list[set[int]] = [set() for _ in fns]
    callers: list[set[int]] = [set() for _ in fns]
    written: dict[str, set[str]] = defaultdict(set)
    for e in graph.edges:
        if e.kind in CALL_KINDS and e.source in idx and e.target in idx and e.source != e.target:
            s, t = idx[e.source], idx[e.target]
            succ[s].add(t)
            callers[t].add(s)
        elif e.kind == "writes":
            written[e.source].add(e.target)

    nn_mask = 0
    for i, f in enumerate(fns):
        if not f.is_noise:
            nn_mask |= 1 << i

    comp_of = [0] * len(fns)
    comps = _sccs(len(fns), succ)
    reach_bits = [0] * len(comps)
    for c, members in enumerate(comps):  # sinks first, so callees are done before callers
        for v in members:
            comp_of[v] = c
    for c, members in enumerate(comps):
        bits = 0
        if len(members) > 1:
            for v in members:
                bits |= 1 << v
        for v in members:
            for w in succ[v]:
                if comp_of[w] != c:
                    bits |= (1 << w) | reach_bits[comp_of[w]]
        reach_bits[c] = bits

    for i, f in enumerate(fns):
        f.fan_in = len(callers[i])
        f.fan_out = len(succ[i])
        f.reach = ((reach_bits[comp_of[i]] & nn_mask) & ~(1 << i)).bit_count()
        f.fields_written_count = len(written[f.id])
    return written


def pick_entry_points(graph: Graph, fns: list[FunctionNode], cfg: RepoConfig) -> None:
    listed = set(cfg.entry_points)
    configured_ids: set[str] = set()
    for f in fns:
        configured = f.qualified_name in listed or any(f.qualified_name.endswith("::" + e) for e in listed)
        if configured:
            configured_ids.add(f.id)
        is_main = f.name == "main" and f.container is None and f.qualified_name == "main"
        heuristic = (
            not f.is_noise
            and f.is_public
            and not f.is_pure_virtual
            and f.fan_in == 0
            and f.fan_out >= MIN_CALLS_FOR_ENTRY_POINT
        )
        f.is_entry_point = configured or is_main or heuristic
    # the ones the repo's own settings name come first (someone chose them), then the rest, biggest reach first
    ranked = sorted((f for f in fns if f.is_entry_point), key=lambda f: (f.id not in configured_ids, -f.reach, f.qualified_name, f.id))
    graph.entry_points = [f.id for f in ranked]


# ---------------------------------------------------------------- driver


def enrich_graph(graph: Graph, cfg: RepoConfig | None = None) -> dict[str, int]:
    cfg = cfg or RepoConfig()
    # start clean so re-running gives the same result
    graph.edges = [e for e in graph.edges if not e.inferred]
    fns = [n for n in graph.nodes if isinstance(n, FunctionNode)]
    for f in fns:
        f.is_noise, f.noise_reason, f.is_entry_point = False, None, False
    by_id = {f.id: f for f in fns}

    n_over = add_override_edges(graph, fns)
    n_may = add_may_call_edges(graph, by_id)
    tag_noise(fns, graph, cfg)
    compute_metrics(graph, fns)
    pick_entry_points(graph, fns, cfg)

    graph.edges.sort(key=lambda e: (e.source, e.kind, e.target, e.dispatch))
    graph.refresh_stats()
    return {
        "overrides_added": n_over,
        "may_call_added": n_may,
        "noise": sum(f.is_noise for f in fns),
        "entry_points": len(graph.entry_points),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m extractor.common.enrich", description=__doc__.split("\n")[0])
    ap.add_argument("graph", help="data/<name>.graph.json (edited in place)")
    ap.add_argument("--config", help="aerotrace.yml to use (default: look it up by repo name)")
    ap.add_argument("--repo", help="repo folder, to find its aerotrace.yml (default: repos/<repo_name>)")
    args = ap.parse_args(argv)

    path = Path(args.graph)
    graph = load_graph(path)
    repo = Path(args.repo) if args.repo else PROJECT_ROOT / "repos" / graph.repo_name
    cfg = find_config(graph.repo_name, repo, path.parent, Path(args.config) if args.config else None)
    if cfg.source:
        print(f"settings from {cfg.source}")
    summary = enrich_graph(graph, cfg)
    save_graph(graph, path)
    print(f"enriched {path}: " + ", ".join(f"{k}={v}" for k, v in summary.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
