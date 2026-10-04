"""CLI: python -m extractor.ada <repo_path> --out data/<name>.graph.json [--project f.gpr]."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from extractor.common.schema import save_graph

from .builder import ModelBuilder
from .convert import ncm_to_graph

MAX_FAILED_FRACTION = 0.05
SKIP_DIRS = {".git", "obj", "lib", "alire", "node_modules", ".venv"}


def find_sources(repo: Path) -> list[Path]:
    """All .ads/.adb files under `repo`, skipping build and VCS directories."""
    return sorted(
        p
        for p in repo.rglob("*")
        if p.suffix in {".ads", ".adb"} and not (SKIP_DIRS & set(p.relative_to(repo).parts))
    )


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m extractor.ada", description=__doc__)
    ap.add_argument("repo", type=Path, help="path to the Ada repo")
    ap.add_argument("--out", type=Path, required=True, help="output graph JSON (shared format)")
    ap.add_argument("--ncm-out", type=Path, help="also write the raw NormalizedCodeModel JSON here")
    ap.add_argument(
        "--project",
        type=Path,
        action="append",
        default=[],
        help=".gpr project file; repeat for several variants (default: a lone root .gpr, else all sources)",
    )
    ap.add_argument(
        "--all-projects",
        action="store_true",
        help="analyse the fewest .gpr variants that together cover the repo (multi-board repos)",
    )
    ap.add_argument(
        "--max-variants",
        type=int,
        default=10,
        help="cap on variants chosen by --all-projects (default 10; each costs about a minute)",
    )
    ap.add_argument(
        "--parser",
        choices=["libadalang", "treesitter"],
        default=None,
        help="force a backend (default: libadalang if importable, else treesitter)",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo: Path = args.repo.resolve()
    if not repo.is_dir():
        print(f"error: {repo} is not a directory", file=sys.stderr)
        return 2

    backend = args.parser
    if backend is None:
        from . import lal

        backend = "libadalang" if lal.available() else "treesitter"
        if backend == "treesitter":
            print(
                "libadalang not installed; falling back to tree-sitter (best-guess links)",
                file=sys.stderr,
            )

    files = find_sources(repo)
    if not files:
        print(f"error: no .ads/.adb files under {repo}", file=sys.stderr)
        return 2

    if backend == "libadalang":
        from . import lal

        if not lal.available():
            print("error: libadalang is not importable; use --parser treesitter", file=sys.stderr)
            return 2
        builder = ModelBuilder(repo, "clang", "PRECISE", {"libadalang": lal.version()})
        projects = [p.resolve() for p in args.project]
        if not projects and not args.all_projects:
            lone = _auto_gpr(repo)
            projects = [lone] if lone else []
        failed, total, label = lal.extract(
            builder, repo, files, projects, args.all_projects, args.max_variants
        )
        project = projects or args.all_projects
        build_context = "COMPLETE" if project else "PARTIAL"
    else:
        from . import treesitter

        if not treesitter.available():
            print(
                "error: tree-sitter-ada is not installed (pip install tree-sitter tree-sitter-ada)",
                file=sys.stderr,
            )
            return 2
        builder = ModelBuilder(repo, "tree_sitter", "FALLBACK_PARSED", {"tree_sitter_ada": "0.1.0"})
        failed, total = treesitter.extract(builder, files)
        label, build_context = "tree-sitter", "UNKNOWN"

    print(f"[{label}] {total - failed}/{total} units parsed cleanly, {failed} with diagnostics")
    if total and failed / total > MAX_FAILED_FRACTION:
        print(
            f"error: {failed} of {total} units ({failed / total:.1%}) failed to parse, more than "
            f"{MAX_FAILED_FRACTION:.0%}. Not writing output. Check the project file / missing "
            "dependencies, or pass --project.",
            file=sys.stderr,
        )
        return 1

    model = builder.build("baseline", build_context, [])
    if args.ncm_out:
        args.ncm_out.parent.mkdir(parents=True, exist_ok=True)
        args.ncm_out.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    graph = ncm_to_graph(model.model_dump(mode="json"), repo.name)
    save_graph(graph, args.out)
    print(f"wrote {args.out}: {graph.stats.nodes}, {graph.stats.edges}")
    print(f"indirect (unresolved) calls: {builder.indirect_calls}")
    return 0


def _auto_gpr(repo: Path) -> Path | None:
    gprs = sorted(p for p in repo.glob("*.gpr"))
    return gprs[0] if len(gprs) == 1 else None
