"""make demo REPO=jsbsim: clone (if missing) -> extract -> enrich -> load -> explain -> serve + web.

Each step is the same command you can run alone from the Makefile. This script runs them in order, times
each one, prints what it found, and stops at the first failure with a plain message.

    python -m scripts.demo jsbsim              # everything, then keep the servers running
    python -m scripts.demo jsbsim --no-serve   # stop after the data is ready (used by the tests)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from extractor.common.config import PROJECT_ROOT, find_config
from extractor.common.schema import load_graph

API_PORT = 8000
WEB_PORT = 5173


@dataclass
class StepResult:
    name: str
    seconds: float
    detail: str = ""
    skipped: bool = False


class StepFailed(Exception):
    def __init__(self, name: str, code: int, tail: str, seconds: float):
        super().__init__(f"{name} failed")
        self.name, self.code, self.tail, self.seconds = name, code, tail, seconds


@dataclass
class Demo:
    repo: str
    data_dir: Path
    repos_dir: Path
    yes: bool = False
    log: callable = print
    results: list[StepResult] = field(default_factory=list)

    # ------------------------------------------------------------ paths
    @property
    def repo_path(self) -> Path:
        return self.repos_dir / self.repo

    @property
    def graph_path(self) -> Path:
        return self.data_dir / f"{self.repo}.graph.json"

    @property
    def db_path(self) -> Path:
        return self.data_dir / f"{self.repo}.db"

    # ------------------------------------------------------------ one step
    def run(self, name: str, cmd: list[str], env: dict[str, str] | None = None) -> str:
        """Run one command. Returns its output; raises StepFailed (with the end of the output) if it fails."""
        self.log(f"> {name} ...")
        t0 = time.perf_counter()
        proc = subprocess.run(
            cmd, cwd=PROJECT_ROOT, env={**os.environ, **(env or {})}, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        seconds = time.perf_counter() - t0
        if proc.returncode != 0:
            tail = "\n".join(proc.stdout.strip().splitlines()[-15:])
            raise StepFailed(name, proc.returncode, tail, seconds)
        return proc.stdout

    def done(self, name: str, seconds: float, detail: str, skipped: bool = False) -> None:
        self.results.append(StepResult(name, seconds, detail, skipped))
        self.log(f"  {'-' if skipped else 'ok'} {name}  {seconds:5.1f}s  {detail}")

    # ------------------------------------------------------------ the steps
    def clone(self) -> None:
        t0 = time.perf_counter()
        if not self.repo_path.exists() or not any(self.repo_path.iterdir()):
            cfg = find_config(self.repo, None, self.data_dir)
            if not cfg.url:
                raise StepFailed("clone", 1, f"repos/{self.repo} is missing and there is no `url:` for it in repos-config/{self.repo}.yml", 0)
            if not shutil.which("git"):
                raise StepFailed("clone", 1, "git is not installed, so the repo cannot be cloned", 0)
            self.run("clone", ["git", "clone", "--depth", "1", cfg.url, str(self.repo_path)])
            verb = "cloned"
        else:
            verb = "already there"
        sha = subprocess.run(["git", "-C", str(self.repo_path), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        self.done("clone", time.perf_counter() - t0, f"{verb}, commit {sha or 'unknown'}")

    def extract(self) -> None:
        t0 = time.perf_counter()
        out = self.run("extract", [sys.executable, "-m", "extractor.cpp", str(self.repo_path), "--out", str(self.graph_path)])
        g = load_graph(self.graph_path)
        m = re.search(r"parse errors: (\d+) in (\d+)/(\d+) files", out)
        errs = f", {m.group(1)} parse errors in {m.group(2)} of {m.group(3)} files" if m else ""
        self.done("extract", time.perf_counter() - t0, f"{g.stats.nodes['function']:,} functions, {g.stats.nodes['field']:,} fields, {g.stats.edges['calls']:,} direct calls{errs}")

    def enrich(self) -> None:
        t0 = time.perf_counter()
        self.run("enrich", [sys.executable, "-m", "extractor.common.enrich", str(self.graph_path)])
        g = load_graph(self.graph_path)
        fns = [n for n in g.nodes if n.kind == "function"]
        self.done(
            "enrich",
            time.perf_counter() - t0,
            f"{g.stats.edges['may_call']:,} virtual links, {g.stats.edges['overrides']:,} overrides, "
            f"{sum(f.is_noise for f in fns):,} helpers tagged, {len(g.entry_points)} entry points",
        )

    def load(self) -> None:
        t0 = time.perf_counter()
        self.run("load", [sys.executable, "-m", "server.store", "load", str(self.graph_path), "--repo-dir", str(self.repo_path)])
        size = self.db_path.stat().st_size / 1e6
        self.done("load", time.perf_counter() - t0, f"{self.db_path.name}, {size:.1f} MB")

    def explain_mode(self) -> tuple[bool, str]:
        """(offline?, why). Offline when asked for, or when there is no way to reach the API."""
        if os.environ.get("AEROTRACE_OFFLINE", "").strip().lower() in ("1", "true", "yes"):
            return True, "AEROTRACE_OFFLINE is set"
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            return True, "no ANTHROPIC_API_KEY"
        return False, ""

    def explain(self) -> None:
        t0 = time.perf_counter()
        offline, why = self.explain_mode()
        cmd = [sys.executable, "-m", "extractor.common.explain", self.repo, "--only-reachable-from-entry-points", "--data-dir", str(self.data_dir)]
        if offline:
            cmd.append("--offline")
        elif self.yes:
            cmd.append("--yes")
        out = self.run("explain", cmd)
        con = sqlite3.connect(self.db_path)
        ai = con.execute("SELECT COUNT(*) FROM summaries WHERE model != 'template'").fetchone()[0]
        tmpl = con.execute("SELECT COUNT(*) FROM summaries WHERE model = 'template'").fetchone()[0]
        bad = con.execute("SELECT COUNT(*) FROM summaries WHERE model != 'template' AND verified = 0").fetchone()[0]
        con.close()
        calls = re.search(r"done: (\d+) API calls", out)
        detail = f"{ai:,} explained by AI ({bad} not fully checked), {tmpl:,} template sentences"
        if calls:
            detail += f", {calls.group(1)} API calls"
        if offline:
            detail += f"; offline ({why}): saved explanations kept, no API calls"
        self.done("explain", time.perf_counter() - t0, detail)
        if offline and ai == 0:
            self.log(
                "  note: there are no AI explanations yet, so functions show 'No explanation yet'. "
                f"Set ANTHROPIC_API_KEY and run `make explain REPO={self.repo}` once, before the demo."
            )

    def pipeline(self) -> None:
        for step in (self.clone, self.extract, self.enrich, self.load, self.explain):
            step()

    # ------------------------------------------------------------ servers
    def serve(self) -> int:
        web = PROJECT_ROOT / "web"
        if not (web / "node_modules").exists():
            raise StepFailed("serve", 1, "web/node_modules is missing. Run: make setup", 0)
        env = {**os.environ, "AEROTRACE_DATA": str(self.data_dir)}
        # Ctrl-C and `kill` both mean "stop" (a shell that started us in the background may have muted Ctrl-C)
        signal.signal(signal.SIGINT, signal.default_int_handler)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        # each server gets its own process group, so stopping it also stops what pnpm started under it
        procs = [
            subprocess.Popen([sys.executable, "-m", "uvicorn", "server.main:app", "--port", str(API_PORT)], cwd=PROJECT_ROOT, env=env, start_new_session=True),
            subprocess.Popen(["pnpm", "dev", "--strictPort"], cwd=web, env=env, start_new_session=True),
        ]
        try:
            for url in (f"http://localhost:{API_PORT}/api/status", f"http://localhost:{WEB_PORT}/"):
                if not wait_for(url, 60):
                    raise StepFailed("serve", 1, f"{url} did not answer within 60 seconds", 0)
            self.log(f"\nReady: http://localhost:{WEB_PORT}/?repo={self.repo}   (Ctrl-C to stop)")
            while all(p.poll() is None for p in procs):
                time.sleep(0.5)
            self.log("\nA server stopped on its own; stopping the other one.")
        except KeyboardInterrupt:
            self.log("\nStopping.")
        finally:
            for p in procs:
                try:
                    os.killpg(p.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
            for p in procs:
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
        return 0


def wait_for(url: str, seconds: float) -> bool:
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def summary(results: list[StepResult]) -> str:
    total = sum(r.seconds for r in results)
    lines = [f"  {r.name:<8} {r.seconds:6.1f}s" for r in results]
    return "\n".join(lines + [f"  {'total':<8} {total:6.1f}s"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.demo", description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="repo name, e.g. jsbsim")
    ap.add_argument("--no-serve", action="store_true", help="stop once the data is ready")
    ap.add_argument("--yes", action="store_true", help="when explaining online, do not ask before spending")
    ap.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"))
    ap.add_argument("--repos-dir", default=str(PROJECT_ROOT / "repos"))
    args = ap.parse_args(argv)

    demo = Demo(Path(args.repo).name, Path(args.data_dir), Path(args.repos_dir), yes=args.yes or os.environ.get("DEMO_YES") == "1")
    demo.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        demo.pipeline()
    except StepFailed as e:
        print(f"\nx {e.name} failed ({'exit ' + str(e.code)}, after {e.seconds:.1f}s). Nothing after it was run.", file=sys.stderr)
        if e.tail:
            print("\n".join("    " + line for line in e.tail.splitlines()), file=sys.stderr)
        print(f"\nFix that, then run again: make demo REPO={demo.repo}  (steps that already finished are cached)", file=sys.stderr)
        return 1
    print("\nData is ready:\n" + summary(demo.results))
    if args.no_serve:
        return 0
    try:
        return demo.serve()
    except StepFailed as e:
        print(f"\nx {e.name} failed: {e.tail}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
