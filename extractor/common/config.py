"""Per-repo settings (aerotrace.yml). Used by the extractor (exclude) and enrich (the rest)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class RepoConfig:
    noise_classes: list[str] = field(default_factory=list)
    noise_name_patterns: list[str] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)  # qualified names
    exclude: list[str] = field(default_factory=list)  # paths / globs the extractor skips
    url: str | None = None  # where to clone the repo from (used by `make demo`)
    source: Path | None = None  # file the settings came from

    @classmethod
    def from_file(cls, path: Path) -> "RepoConfig":
        raw = yaml.safe_load(path.read_text()) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: expected a mapping at the top level")
        known = {"noise_classes", "noise_name_patterns", "entry_points", "exclude", "url"}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"{path}: unknown setting(s) {sorted(unknown)}; allowed: {sorted(known)}")
        kw = {k: [str(x) for x in (raw.get(k) or [])] for k in known - {"url"}}
        return cls(source=path, url=str(raw["url"]) if raw.get("url") else None, **kw)


def find_config(
    repo_name: str,
    repo_path: Path | None = None,
    data_dir: Path | None = None,
    explicit: Path | None = None,
) -> RepoConfig:
    """First match wins: explicit path, <repo>/aerotrace.yml, <data>/<name>.yml, repos-config/<name>.yml."""
    if explicit is not None:
        return RepoConfig.from_file(explicit)
    candidates: list[Path] = []
    if repo_path is not None:
        candidates.append(repo_path / "aerotrace.yml")
    candidates.append((data_dir or PROJECT_ROOT / "data") / f"{repo_name}.yml")
    candidates.append(PROJECT_ROOT / "repos-config" / f"{repo_name}.yml")
    for c in candidates:
        if c.is_file():
            return RepoConfig.from_file(c)
    return RepoConfig()
