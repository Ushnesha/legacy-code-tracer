"""Orchestration: flags -> parallel parse (with cache) -> merge/dedupe -> Graph."""

from __future__ import annotations

import hashlib
import json
import multiprocessing as mp
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from extractor.common.schema import Edge, FieldNode, FileNode, FunctionNode, Graph

from . import parse as parse_mod
from .flags import (
    CompileUnit,
    DEFAULT_EXCLUDES,
    find_compile_units,
    system_include_args,
)

EXTRACTOR_VERSION = "4"  # bump when parse output changes, to invalidate caches
MAX_ERROR_FILE_RATIO = 0.05


@dataclass
class Report:
    flags_source: str = ""
    files_total: int = 0
    files_with_errors: int = 0
    error_count: int = 0
    cache_hits: int = 0
    errors_by_file: dict[str, list[str]] = field(default_factory=dict)
    dropped_edges: int = 0

    @property
    def too_many_errors(self) -> bool:
        return self.files_total > 0 and self.files_with_errors / self.files_total > MAX_ERROR_FILE_RATIO


# ---------------------------------------------------------------- cache


def _args_hash(unit: CompileUnit, extra: list[str], repo: Path, excludes: list[str]) -> str:
    blob = json.dumps([EXTRACTOR_VERSION, str(repo), sorted(excludes), unit.directory, unit.args, extra])
    return hashlib.sha256(blob.encode()).hexdigest()


def _cache_path(cache_dir: Path, unit: CompileUnit) -> Path:
    return cache_dir / (hashlib.sha256(unit.file.encode()).hexdigest()[:24] + ".json")


def _cache_load(path: Path, args_hash: str) -> dict | None:
    """A cached result is valid if the args match and every file it depended on is unchanged."""
    try:
        entry = json.loads(path.read_text())
        if entry["args_hash"] != args_hash:
            return None
        for dep, h in entry["result"]["deps"].items():
            if parse_mod.file_hash(dep) != h:
                return None
        return entry["result"]
    except (OSError, ValueError, KeyError):
        return None


def _cache_save(path: Path, args_hash: str, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"args_hash": args_hash, "result": result}))
    tmp.replace(path)


# ---------------------------------------------------------------- worker


def _work(job: tuple) -> tuple[str, dict]:
    unit, repo, excludes, extra = job
    return unit.file, parse_mod.parse_unit(unit, Path(repo), excludes, extra)


# ---------------------------------------------------------------- git


def _git(repo: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and out else None


# ---------------------------------------------------------------- merge


def merge(results: list[dict], repo: Path, report: Report) -> Graph:
    functions: dict[str, dict] = {}
    fields: dict[str, dict] = {}
    files: set[str] = set()
    class_bases: dict[str, list[str]] = {}
    class_docs: dict[str, str] = {}
    # Headers are parsed once per including file: dedupe by USR, first definition wins
    # (results are sorted by source file, so this is deterministic). Edges come with it.
    for res in results:
        files.update(res["files"])
        for cls, bases in res.get("class_bases", {}).items():
            known = class_bases.setdefault(cls, [])
            known.extend(b for b in bases if b not in known)
        for cls, doc in res.get("class_docs", {}).items():
            class_docs.setdefault(cls, doc)
        for rec in res["functions"]:
            cur = functions.get(rec["node"]["id"])
            # a real definition beats a bodiless pure-virtual declaration of the same method
            if cur is None or (cur["node"]["is_pure_virtual"] and not rec["node"]["is_pure_virtual"]):
                functions[rec["node"]["id"]] = rec
        for fld in res["fields"]:
            fields.setdefault(fld["id"], fld)

    edges: list[Edge] = []

    def add(src: str, dst: str, kind: str, lines: list[int], dispatch: bool = False) -> None:
        edges.append(
            Edge(source=src, target=dst, kind=kind, count=max(len(lines), 1), lines=sorted(lines), dispatch=dispatch)
        )

    nodes: list = []
    for usr, rec in sorted(functions.items(), key=lambda kv: (kv[1]["node"]["file"], kv[1]["node"]["line_start"], kv[0])):
        unresolved = rec["unresolved_calls"]
        for target, kind, lines, dispatch in rec["calls"]:
            if target in functions:
                add(usr, target, kind, lines, dispatch)
            else:
                # declared in the repo but no definition seen: record, never guess
                unresolved += len(lines)
                report.dropped_edges += 1
        for target, kind, lines in rec["accesses"]:
            if target in fields:
                add(usr, target, kind, lines)
            else:
                report.dropped_edges += 1
        for target in rec["overrides"]:
            if target in functions:
                add(usr, target, "overrides", [])
        nodes.append(
            FunctionNode(
                **rec["node"],
                recursive=rec["recursive"],
                indirect_calls=sorted(rec["indirect_calls"]),
                external_calls=rec["external_calls"],
                unresolved_calls=unresolved,
            )
        )
        add(usr, f"file:{rec['node']['file']}", "defined_in", [rec["node"]["line_start"]])

    for usr, fld in sorted(fields.items(), key=lambda kv: (kv[1]["file"], kv[1]["line"], kv[0])):
        nodes.append(FieldNode(**fld))
        add(usr, f"file:{fld['file']}", "defined_in", [fld["line"]])

    suffix_lang = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}
    for path in sorted(files):
        if Path(path).suffix.lower() in suffix_lang:
            nodes.append(FileNode(id=f"file:{path}", path=path, language="cpp"))

    edges.sort(key=lambda e: (e.source, e.kind, e.target))
    graph = Graph(
        repo_name=repo.name,
        repo_url=_git(repo, "remote", "get-url", "origin"),
        commit_sha=_git(repo, "rev-parse", "HEAD"),
        language="cpp",
        extracted_at=datetime.now(timezone.utc),
        nodes=nodes,
        edges=edges,
        class_bases=dict(sorted(class_bases.items())),
        class_docs=dict(sorted(class_docs.items())),
    )
    graph.refresh_stats()
    return graph


# ---------------------------------------------------------------- entry point


def extract_repo(
    repo_path: str | Path,
    *,
    cache_dir: Path | None,
    build_dir: Path | None = None,
    excludes: list[str] | None = None,
    jobs: int | None = None,
    log=print,
) -> tuple[Graph, Report]:
    repo = Path(os.path.realpath(repo_path))
    if not repo.is_dir():
        raise SystemExit(f"error: {repo} is not a directory")
    excludes = list(DEFAULT_EXCLUDES) + list(excludes or [])
    report = Report()

    units, report.flags_source = find_compile_units(repo, build_dir.resolve() if build_dir else None, excludes)
    report.files_total = len(units)
    if not units:
        raise SystemExit("error: no C/C++ source files found to parse")
    log(f"flags from: {report.flags_source}; {len(units)} source files")

    extra = system_include_args()
    results: dict[str, dict] = {}
    todo: list[tuple[CompileUnit, str]] = []
    for u in units:
        ah = _args_hash(u, extra, repo, excludes)
        hit = _cache_load(_cache_path(cache_dir, u), ah) if cache_dir else None
        if hit is not None:
            results[u.file] = hit
            report.cache_hits += 1
        else:
            todo.append((u, ah))
    log(f"cache: {report.cache_hits} hit, {len(todo)} to parse")

    jobs = max(1, jobs or os.cpu_count() or 1)
    jobs_list = [(u, str(repo), excludes, extra) for u, _ in todo]
    hashes = {u.file: ah for u, ah in todo}
    if jobs_list:
        if jobs == 1 or len(jobs_list) == 1:
            outputs = map(_work, jobs_list)
            pool = None
        else:
            pool = mp.get_context("spawn").Pool(min(jobs, len(jobs_list)))
            outputs = pool.imap_unordered(_work, jobs_list)
        try:
            for file, res in outputs:
                results[file] = res
                if cache_dir:
                    _cache_save(_cache_path(cache_dir, next(u for u, _ in todo if u.file == file)), hashes[file], res)
        finally:
            if pool:
                pool.close()
                pool.join()

    ordered = [results[u.file] for u in sorted(units, key=lambda u: u.file)]
    for res in ordered:
        if res["error_count"]:
            report.files_with_errors += 1
            report.error_count += res["error_count"]
            report.errors_by_file[res["file"]] = res["errors"]

    for f, errs in report.errors_by_file.items():
        log(f"parse errors in {f}:")
        for e in errs[:10]:
            log(f"  {e}")
    log(
        f"parse errors: {report.error_count} in {report.files_with_errors}/{report.files_total} files"
    )
    if report.too_many_errors:
        raise ParseErrorLimit(report)

    return merge(ordered, repo, report), report


class ParseErrorLimit(Exception):
    def __init__(self, report: Report):
        self.report = report
        super().__init__(
            f"{report.files_with_errors} of {report.files_total} files had parse errors "
            f"(limit is {MAX_ERROR_FILE_RATIO:.0%}); refusing to write a half-finished graph"
        )
