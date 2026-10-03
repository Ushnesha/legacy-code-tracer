"""CLI: python -m extractor.cpp <repo_path> --out data/<name>.graph.json"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extractor.common.config import find_config
from extractor.common.schema import save_graph

from .build import ParseErrorLimit, extract_repo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m extractor.cpp", description="Extract a call graph and data-access graph from a C/C++ repo.")
    ap.add_argument("repo_path")
    ap.add_argument("--out", required=True, help="where to write the graph JSON")
    ap.add_argument("--build-dir", help="folder holding compile_commands.json")
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="extra path to skip (repeatable)")
    ap.add_argument("--config", help="aerotrace.yml to read `exclude` from (default: look it up by repo name)")
    ap.add_argument("--jobs", type=int, default=None, help="parallel parsers (default: CPU count)")
    ap.add_argument("--no-cache", action="store_true", help="re-parse everything")
    args = ap.parse_args(argv)

    out = Path(args.out)
    repo = Path(args.repo_path).resolve()
    cfg = find_config(repo.name, repo, out.parent, Path(args.config) if args.config else None)
    if cfg.source:
        print(f"settings from {cfg.source}")
    cache_dir = None if args.no_cache else out.parent / "cache" / Path(args.repo_path).resolve().name
    try:
        graph, report = extract_repo(
            args.repo_path,
            cache_dir=cache_dir,
            build_dir=Path(args.build_dir) if args.build_dir else None,
            excludes=cfg.exclude + args.exclude,
            jobs=args.jobs,
        )
    except ParseErrorLimit as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    save_graph(graph, out)
    s = graph.stats
    print(f"wrote {out}: {s.nodes} nodes, {s.edges} edges")
    return 0


if __name__ == "__main__":
    sys.exit(main())
