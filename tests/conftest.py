"""Shared fixtures: real repos extracted + enriched once per test session."""

import subprocess
from pathlib import Path

import pytest

from extractor.common.config import find_config
from extractor.common.enrich import enrich_graph
from extractor.cpp.build import extract_repo

ROOT = Path(__file__).resolve().parent.parent
REPOS = {
    "tinyxml2": ("https://github.com/leethomason/tinyxml2", "tinyxml2.cpp"),
    "jsbsim": ("https://github.com/JSBSim-Team/jsbsim", "src/FGFDMExec.cpp"),
}


def ensure_repo(name: str) -> Path:
    url, marker = REPOS[name]
    path = ROOT / "repos" / name
    if not (path / marker).exists():
        r = subprocess.run(["git", "clone", "--depth", "1", url, str(path)], capture_output=True, text=True)
        if r.returncode != 0:
            pytest.skip(f"could not clone {name}: {r.stderr[-200:]}")
    return path


def build_graph(name: str, cache_dir: Path):
    repo = ensure_repo(name)
    cfg = find_config(name, repo)
    graph, _ = extract_repo(repo, cache_dir=cache_dir, excludes=cfg.exclude, jobs=4, log=lambda *_: None)
    enrich_graph(graph, cfg)
    return graph


@pytest.fixture(scope="session")
def tinyxml2_graph(tmp_path_factory):
    return build_graph("tinyxml2", tmp_path_factory.mktemp("cache_tinyxml2"))


@pytest.fixture(scope="session")
def jsbsim_graph(tmp_path_factory):
    return build_graph("jsbsim", tmp_path_factory.mktemp("cache_jsbsim"))
