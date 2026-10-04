"""Backend-independent builder for the NormalizedCodeModel (docs/CONTRACTS.md §5).

Both Ada backends (libadalang, tree-sitter) feed this builder so IDs, evidence and the
"every edge has evidence" rule are identical regardless of how the facts were found.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aerotrace.contracts.ids import hash_bytes, make_id
from aerotrace.contracts.models import (
    AnalysisMethod,
    Edge,
    EdgeType,
    Evidence,
    FileCoverage,
    FileStatus,
    Node,
    NodeKind,
    NormalizedCodeModel,
    Quality,
    RunManifest,
)

LANGUAGE = "ada"
MAX_SNIPPET = 160


class SourceFile:
    """Holds one source file's bytes and converts (line, column) to byte offsets."""

    def __init__(self, path: Path, rel: str) -> None:
        self.rel = rel
        self.data = path.read_bytes()
        self._lines = self.data.decode("utf-8", errors="replace").splitlines(keepends=True)
        self._starts: list[int] = []
        off = 0
        for line in self._lines:
            self._starts.append(off)
            off += len(line.encode("utf-8", errors="replace"))

    def offset(self, line: int, col: int) -> int:
        """Byte offset of 1-based (line, col); col counts characters, as libadalang does."""
        if line < 1 or line > len(self._lines):
            return len(self.data)
        text = self._lines[line - 1]
        return self._starts[line - 1] + len(text[: max(col - 1, 0)].encode("utf-8", "replace"))


class ModelBuilder:
    """Accumulates nodes, edges, evidence and coverage, deduplicating by ID."""

    def __init__(
        self,
        repo: Path,
        method: AnalysisMethod,
        quality: Quality,
        analyzer_versions: dict[str, str],
    ) -> None:
        self.repo = repo.resolve()
        self.method: AnalysisMethod = method
        self.quality: Quality = quality
        self.analyzer_versions = analyzer_versions
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str, int], Edge] = {}
        self.evidence: dict[str, Evidence] = {}
        self.coverage: dict[str, FileCoverage] = {}
        self.known_gaps: list[str] = []
        self.indirect_calls = 0
        self.counters: dict[str, int] = defaultdict(int)
        self._files: dict[str, SourceFile] = {}
        self._file_nodes: dict[str, str] = {}

    # --- files / evidence ---------------------------------------------------------------

    def rel(self, path: str | Path) -> str:
        """Repo-relative, forward-slash path (falls back to the path as given)."""
        p = Path(path).resolve()
        try:
            return p.relative_to(self.repo).as_posix()
        except ValueError:
            return p.as_posix()

    def source(self, path: str | Path) -> SourceFile:
        rel = self.rel(path)
        if rel not in self._files:
            self._files[rel] = SourceFile(Path(path), rel)
        return self._files[rel]

    def add_evidence(
        self,
        path: str | Path,
        start: tuple[int, int],
        end: tuple[int, int],
        quality: Quality | None = None,
    ) -> str:
        """Create (or reuse) an Evidence for a (line, col)..(line, col) span; returns its ID."""
        src = self.source(path)
        s, e = src.offset(*start), src.offset(*end)
        e = max(e, s)
        raw = src.data[s:e]
        ev_id = make_id(src.rel, str(s), str(e), hash_bytes(raw))
        if ev_id not in self.evidence:
            snippet = raw.decode("utf-8", errors="replace").strip().splitlines()
            self.evidence[ev_id] = Evidence(
                evidence_id=ev_id,
                file=src.rel,
                start_line=start[0],
                end_line=end[0],
                start_byte=s,
                end_byte=e,
                snippet_hash=hash_bytes(raw),
                snippet=(snippet[0][:MAX_SNIPPET] if snippet else None),
                analysis_method=self.method,
                quality=quality or self.quality,
            )
        return ev_id

    def set_coverage(self, path: str | Path, status: FileStatus, reason: str | None = None) -> None:
        rel = self.rel(path)
        self.coverage[rel] = FileCoverage(file=rel, status=status, reason=reason)

    # --- nodes --------------------------------------------------------------------------

    def add_node(
        self,
        kind: NodeKind,
        name: str,
        qualified_name: str,
        file: str | Path | None = None,
        span: tuple[tuple[int, int], tuple[int, int]] | None = None,
        signature: str | None = None,
        properties: dict[str, Any] | None = None,
        quality: Quality | None = None,
    ) -> str:
        """Add a node (idempotent). `properties` always gets language='ada'."""
        rel = self.rel(file) if file is not None else ""
        node_id = make_id(kind, rel, qualified_name, signature or "")
        if node_id in self.nodes:
            return node_id
        ev_ids: list[str] = []
        start_line = end_line = None
        if file is not None and span is not None:
            ev_ids = [self.add_evidence(file, span[0], span[1], quality)]
            start_line, end_line = span[0][0], span[1][0]
        props = {"language": LANGUAGE, **(properties or {})}
        self.nodes[node_id] = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified_name,
            file=rel or None,
            start_line=start_line,
            end_line=end_line,
            signature=signature,
            evidence_ids=ev_ids,
            properties=props,
        )
        return node_id

    def file_node(self, path: str | Path) -> str:
        rel = self.rel(path)
        if rel not in self._file_nodes:
            src = self.source(path)
            end = (len(src._lines) + 1, 1)
            self._file_nodes[rel] = self.add_node("File", Path(rel).name, rel, path, ((1, 1), end))
        return self._file_nodes[rel]

    def external(self, name: str, file: str | Path, line: int, reason: str, ev_id: str) -> str:
        """An ExternalSymbol node describing an unresolvable call target."""
        rel = self.rel(file)
        node_id = make_id("ExternalSymbol", rel, f"{line}:{name}")
        if node_id not in self.nodes:
            self.nodes[node_id] = Node(
                id=node_id,
                kind="ExternalSymbol",
                name=name,
                qualified_name=f"{rel}:{line}:{name}",
                file=rel,
                start_line=line,
                end_line=line,
                evidence_ids=[ev_id],
                properties={"language": LANGUAGE, "reason": reason},
            )
        return node_id

    # --- edges --------------------------------------------------------------------------

    def add_edge(
        self,
        source: str,
        target: str,
        etype: EdgeType,
        ev_id: str,
        properties: dict[str, Any] | None = None,
        line: int = 0,
    ) -> bool:
        """Add an edge; the same (source, target, type, line) is only recorded once."""
        key = (source, target, etype, line)
        if key in self.edges:
            return False
        self.edges[key] = Edge(
            source=source,
            target=target,
            type=etype,
            evidence_ids=[ev_id],
            properties=({"language": LANGUAGE} | (properties or {})),
        )
        return True

    # --- result -------------------------------------------------------------------------

    def build(
        self, variant: str, build_context: str, include_paths: list[str]
    ) -> NormalizedCodeModel:
        ids = sorted(self.nodes)
        run_id = make_id(
            "ada",
            variant,
            self.method,
            *sorted(include_paths),
            *sorted(self.analyzer_versions.values()),
        )
        manifest = RunManifest(
            run_id=run_id,
            repo_path=self.repo.as_posix(),
            commit=None,
            dirty=False,
            variant=variant,
            defines=[],
            include_paths=include_paths,
            build_context=build_context,  # type: ignore[arg-type]
            analyzer_versions=self.analyzer_versions,
            created_at=datetime.now(UTC),
        )
        del ids
        return NormalizedCodeModel(
            manifest=manifest,
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            evidence=list(self.evidence.values()),
            coverage=list(self.coverage.values()),
            known_gaps=self.known_gaps,
        )
