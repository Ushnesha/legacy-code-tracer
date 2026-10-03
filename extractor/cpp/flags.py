"""Step A + B: find compile flags for every source file and clean them up for libclang."""

from __future__ import annotations

import fnmatch
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_EXTS = {".c", ".cc", ".cpp", ".cxx"}
HEADER_EXTS = {".h", ".hh", ".hpp", ".hxx"}
DEFAULT_EXCLUDES = ["third_party/", "external/", "vendor/", "build/"]


@dataclass
class CompileUnit:
    file: str  # absolute path of the source file
    directory: str  # working directory the args are relative to
    args: list[str] = field(default_factory=list)  # cleaned args, no compiler / -c / -o / source


def warn(msg: str) -> None:
    print(f"warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- excludes


def is_excluded(rel_path: str, patterns: list[str]) -> bool:
    """Match a repo-relative path against exclude patterns.

    "dir/" matches that folder at any depth; anything else is an fnmatch glob tried
    against the full path, the file name, and every parent folder.
    """
    rel = rel_path.replace(os.sep, "/")
    parts = rel.split("/")
    for pat in patterns:
        if pat.endswith("/"):
            if ("/" + rel).find("/" + pat) != -1:
                return True
            continue
        if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(parts[-1], pat):
            return True
        if any(fnmatch.fnmatch("/".join(parts[: i + 1]), pat) for i in range(len(parts) - 1)):
            return True
    return False


def in_repo(path: str | Path, repo: Path, excludes: list[str]) -> str | None:
    """Return the repo-relative posix path if `path` is inside the repo and not excluded."""
    try:
        rel = Path(os.path.realpath(path)).relative_to(repo)
    except ValueError:
        return None
    rel_s = rel.as_posix()
    return None if is_excluded(rel_s, excludes) else rel_s


# ---------------------------------------------------------------- arg cleaning

_DROP_WITH_VALUE = {"-o", "-MF", "-MT", "-MQ"}
_DROP_FLAGS = {"-c", "-MD", "-MMD", "-MP", "-MG"}


def clean_args(args: list[str], source_file: str, directory: str) -> list[str]:
    """Drop the compiler path, -c, the source filename, and -o <value> (plus dependency-file flags)."""
    src_real = os.path.realpath(os.path.join(directory, source_file))
    out: list[str] = []
    it = iter(args[1:])  # args[0] is the compiler
    for a in it:
        if a in _DROP_WITH_VALUE:
            next(it, None)
            continue
        if a in _DROP_FLAGS:
            continue
        if a.startswith("-o") and len(a) > 2 and not a.startswith("-obj"):
            continue  # "-ofile" form
        if not a.startswith("-") and os.path.realpath(os.path.join(directory, a)) == src_real:
            continue
        out.append(a)
    return out


def parse_search_list(text: str) -> list[str]:
    """Parse the output of `<cc> -xc++ -E -v -` into -isystem / -iframework args."""
    args: list[str] = []
    inside = False
    for line in text.splitlines():
        if "#include <...> search starts here:" in line:
            inside = True
            continue
        if "End of search list." in line:
            break
        if not inside:
            continue
        p = line.strip()
        if not p:
            continue
        if p.endswith("(framework directory)"):
            args.append("-iframework" + p[: -len("(framework directory)")].strip())
        else:
            args.append("-isystem" + p)
    return args


_system_args_cache: list[str] | None = None


def system_include_args() -> list[str]:
    """Ask g++ (or clang++) where its system headers live; pip libclang ships none."""
    global _system_args_cache
    if _system_args_cache is not None:
        return _system_args_cache
    for cc in ("g++", "clang++"):
        if not shutil.which(cc):
            continue
        try:
            r = subprocess.run(
                [cc, "-xc++", "-E", "-v", "-"],
                input="",
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        args = parse_search_list(r.stderr)
        if args:
            _system_args_cache = args
            return args
    warn("neither g++ nor clang++ gave a system header list; standard headers may fail to resolve")
    _system_args_cache = []
    return _system_args_cache


# ---------------------------------------------------------------- Step A


def _from_compile_db(db_dir: Path, repo: Path, excludes: list[str]) -> list[CompileUnit]:
    from clang.cindex import CompilationDatabase

    db = CompilationDatabase.fromDirectory(str(db_dir))
    units: dict[str, CompileUnit] = {}
    for cmd in db.getAllCompileCommands():
        directory = cmd.directory
        path = os.path.realpath(os.path.join(directory, cmd.filename))
        if in_repo(path, repo, excludes) is None or path in units:
            continue
        args = clean_args(list(cmd.arguments), cmd.filename, directory)
        units[path] = CompileUnit(path, directory, args)
    return list(units.values())


def _header_dirs(repo: Path, excludes: list[str]) -> list[str]:
    found: set[str] = set()
    for root, dirs, files in os.walk(repo):
        rel_root = Path(root).relative_to(repo).as_posix()
        prefix = "" if rel_root == "." else rel_root + "/"
        dirs[:] = [
            d for d in dirs if d != ".git" and not is_excluded(f"{prefix}{d}/x", excludes)
        ]
        if any(Path(f).suffix.lower() in HEADER_EXTS for f in files):
            found.add(os.path.realpath(root))
    return sorted(found)


def _fallback(repo: Path, excludes: list[str]) -> list[CompileUnit]:
    warn(
        "no compile_commands.json available; guessing flags (-x c++ -std=c++17 plus -I for every "
        "header folder). Results may be less accurate."
    )
    incs = [f"-I{d}" for d in _header_dirs(repo, excludes)]
    units = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in sorted(files):
            if Path(f).suffix.lower() not in SOURCE_EXTS:
                continue
            full = os.path.realpath(os.path.join(root, f))
            if in_repo(full, repo, excludes) is None:
                continue
            units.append(CompileUnit(full, str(repo), ["-x", "c++", "-std=c++17", *incs]))
    return sorted(units, key=lambda u: u.file)


def find_compile_units(
    repo: Path, build_dir: Path | None, excludes: list[str]
) -> tuple[list[CompileUnit], str]:
    """Returns (units with cleaned args, description of where the flags came from)."""
    candidates = []
    if build_dir is not None:
        candidates.append(build_dir)
    candidates.append(repo / "build")
    for d in candidates:
        if (d / "compile_commands.json").is_file():
            return _from_compile_db(d, repo, excludes), f"{d}/compile_commands.json"

    if (repo / "CMakeLists.txt").is_file():
        if shutil.which("cmake") is None:
            warn("CMakeLists.txt found but cmake is not installed")
        else:
            target = build_dir or (repo / "build")
            r = subprocess.run(
                ["cmake", "-S", str(repo), "-B", str(target), "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON"],
                capture_output=True,
                text=True,
            )
            if r.returncode == 0 and (target / "compile_commands.json").is_file():
                return _from_compile_db(target, repo, excludes), "cmake configure"
            warn("cmake configure failed:\n" + (r.stderr or r.stdout)[-1500:])

    return _fallback(repo, excludes), "fallback guess"
