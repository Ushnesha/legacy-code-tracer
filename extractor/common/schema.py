"""Language-neutral graph format shared by every extractor, the server and the web app.

This module is a CONTRACT. Later steps may add optional fields, but must never
rename or remove existing ones.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["cpp", "ada"]

NodeKind = Literal["function", "field", "file"]
EdgeKind = Literal["calls", "may_call", "overrides", "reads", "writes", "defined_in"]

NODE_KINDS: tuple[str, ...] = ("function", "field", "file")
EDGE_KINDS: tuple[str, ...] = (
    "calls",
    "may_call",
    "overrides",
    "reads",
    "writes",
    "defined_in",
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FunctionNode(_Model):
    kind: Literal["function"] = "function"
    id: str  # stable and unique; for C++ this is the clang USR
    name: str  # short name, e.g. "Run"
    qualified_name: str  # e.g. "JSBSim::FGFDMExec::Run"
    container: str | None = None  # class / package name
    language: Language
    file: str  # path relative to the repo root
    line_start: int
    line_end: int
    signature: str
    is_virtual: bool = False
    is_entry_point: bool = False
    is_noise: bool = False
    loc: int = 0
    doc_comment: str | None = None
    summary: str | None = None  # filled in later by the LLM
    # Optional fields added by the C++ extractor:
    recursive: bool = False  # the function calls itself
    external_calls: int = 0  # calls to functions outside the repo (std::, libc, ...)
    unresolved_calls: int = 0  # calls to repo functions with no definition seen; never guessed
    # Added by the extractor (step 3):
    indirect_calls: list[int] = Field(default_factory=list)  # lines of calls through a pointer
    param_types: list[str] | None = None
    is_public: bool = True
    is_pure_virtual: bool = False  # declaration only, no body
    # Added by the enrich pass:
    noise_reason: str | None = None
    fan_in: int = 0
    fan_out: int = 0
    reach: int = 0
    fields_written_count: int = 0


class FieldNode(_Model):
    kind: Literal["field"] = "field"
    id: str
    name: str
    qualified_name: str
    container: str | None = None
    type: str
    file: str
    line: int


class FileNode(_Model):
    kind: Literal["file"] = "file"
    id: str
    path: str
    language: Language


Node = Annotated[Union[FunctionNode, FieldNode, FileNode], Field(discriminator="kind")]


class Edge(_Model):
    """calls / may_call / overrides: function -> function
    reads / writes: function -> field
    defined_in: function or field -> file
    """

    source: str
    target: str
    kind: EdgeKind
    count: int = 1
    lines: list[int] = Field(default_factory=list)  # call-site line numbers
    dispatch: bool = False  # calls edge: goes through a pointer/reference to a virtual method
    inferred: bool = False  # added by the enrich pass (safe to throw away and recompute)


class GraphStats(_Model):
    nodes: dict[str, int] = Field(default_factory=dict)  # count per node kind
    edges: dict[str, int] = Field(default_factory=dict)  # count per edge kind


class Graph(_Model):
    repo_name: str
    repo_url: str | None = None
    commit_sha: str | None = None
    language: Language
    extracted_at: datetime
    stats: GraphStats = Field(default_factory=GraphStats)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    class_bases: dict[str, list[str]] = Field(default_factory=dict)  # class -> base classes
    entry_points: list[str] = Field(default_factory=list)  # function ids, biggest reach first
    class_docs: dict[str, str] = Field(default_factory=dict)  # class -> its doc comment

    def compute_stats(self) -> GraphStats:
        """Count nodes and edges per kind (every kind is present, even at 0)."""
        node_counts = {k: 0 for k in NODE_KINDS}
        for n in self.nodes:
            node_counts[n.kind] += 1
        edge_counts = {k: 0 for k in EDGE_KINDS}
        for e in self.edges:
            edge_counts[e.kind] += 1
        return GraphStats(nodes=node_counts, edges=edge_counts)

    def refresh_stats(self) -> None:
        self.stats = self.compute_stats()


def save_graph(graph: Graph, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")


def load_graph(path: str | Path) -> Graph:
    return Graph.model_validate_json(Path(path).read_text(encoding="utf-8"))
