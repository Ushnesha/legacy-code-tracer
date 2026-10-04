"""Shared data shapes for every AeroTrace stream (docs/CONTRACTS.md §4-5). LOCKED."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

# --- Enums (CONTRACTS §4) ---------------------------------------------------------------------

NodeKind = Literal["Module", "File", "Function", "Variable", "ExternalSymbol", "Test"]
EdgeType = Literal[
    "CONTAINS",
    "DEFINES",
    "INCLUDES",
    "CALLS",
    "MAY_CALL",
    "UNRESOLVED_CALL",
    "READS",
    "WRITES",
    "GUARDED_BY",
    "VERIFIES",
]
Origin = Literal["DETERMINISTIC_ANALYZER", "CONFIG_PARSER", "TEMPLATE", "LLM_NARRATIVE", "HUMAN"]
AnalysisMethod = Literal["clang", "tree_sitter", "config", "regex"]
Quality = Literal["PRECISE", "FALLBACK_PARSED"]
BuildContext = Literal["COMPLETE", "PARTIAL", "UNKNOWN"]
FileStatus = Literal["PARSED", "FALLBACK_PARSED", "FAILED", "SKIPPED"]
Classification = Literal["SUPPORTED", "INFERRED", "ASSUMED", "UNKNOWN", "CONFLICTING"]
ClaimStatus = Literal[
    "UNREVIEWED", "ACCEPTED", "EDITED", "REJECTED", "NEEDS_INVESTIGATION", "STALE"
]
ReviewAction = Literal["ACCEPT", "EDIT", "REJECT", "INVESTIGATE"]
ArtifactKind = Literal[
    "function_card", "call_tree", "data_flow", "sequence", "module_card", "impact_dossier"
]

DEFAULT_BANNER = (
    "Engineering aid — not certification evidence. "
    "Advisory; requires independent engineering review."
)


# --- Models (CONTRACTS §5) --------------------------------------------------------------------


class Evidence(BaseModel):
    evidence_id: str
    file: str  # repo-relative, forward slashes
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int
    snippet_hash: str  # sha256 of the exact source bytes in the span
    snippet: str | None = None  # optional short copy for display
    analysis_method: AnalysisMethod
    quality: Quality


class Node(BaseModel):
    id: str
    kind: NodeKind
    name: str
    qualified_name: str
    file: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    signature: str | None = None
    origin: Origin = "DETERMINISTIC_ANALYZER"
    evidence_ids: list[str] = []
    properties: dict[str, Any] = {}  # e.g. {"static": true, "volatile": true, "ifdef": "..."}


class Edge(BaseModel):
    source: str
    target: str  # for UNRESOLVED_CALL: an ExternalSymbol node describing the unknown target
    type: EdgeType
    origin: Origin = "DETERMINISTIC_ANALYZER"
    evidence_ids: list[str]  # must be non-empty
    properties: dict[
        str, Any
    ] = {}  # CALLS: {"call_site_line": 145, "condition": "...", "order": 3}


class FileCoverage(BaseModel):
    file: str
    status: FileStatus
    reason: str | None = None  # e.g. "missing header nav_gen.h", "binary", "vendor"


class RunManifest(BaseModel):
    run_id: str
    repo_path: str
    commit: str | None
    dirty: bool
    variant: str
    defines: list[str]
    include_paths: list[str]
    build_context: BuildContext
    analyzer_versions: dict[str, str]
    created_at: datetime


class NormalizedCodeModel(BaseModel):
    manifest: RunManifest
    nodes: list[Node]
    edges: list[Edge]
    evidence: list[Evidence]
    coverage: list[FileCoverage]
    known_gaps: list[str]  # human-readable, e.g. "unresolved callback at dispatch.c:88"


class TraversalLimits(BaseModel):
    max_depth: int = 6
    max_nodes: int = 500
    max_edges: int = 1500
    timeout_s: float = 5.0


class PathHop(BaseModel):
    source: str
    target: str
    edge_type: EdgeType
    evidence_ids: list[str]


class ImpactItem(BaseModel):
    node_id: str
    name: str
    path: list[PathHop]  # full path from seed to this node
    reason: str  # short deterministic reason


class ImpactResult(BaseModel):
    run_id: str
    seed_ids: list[str]
    direct: list[ImpactItem]
    transitive: list[ImpactItem]
    verification: list[ImpactItem]
    uncertain_frontier: list[ImpactItem]
    truncated: bool = False
    truncation_reason: str | None = None
    empty_message: str | None = None  # fixed wording from DESIGN §4.10 when nothing found


class ContextBundle(BaseModel):
    task: ArtifactKind
    run_scope: dict[str, Any]  # {"run_id", "commit", "variant", "build_context"}
    subject: dict[str, Any]  # {"symbol_id", "name", "signature"}
    facts: list[dict[str, Any]]  # structured facts from the graph
    evidence: list[Evidence]
    allowed_evidence_ids: list[str]
    known_gaps: list[str]


class Claim(BaseModel):
    claim_id: str
    run_id: str
    artifact_id: str
    subject_id: str
    text: str
    classification: Classification
    evidence_ids: list[str]
    origin: Origin  # TEMPLATE | LLM_NARRATIVE | HUMAN
    status: ClaimStatus = "UNREVIEWED"
    revision: int = 0
    assumptions: list[str] = []
    open_questions: list[str] = []


class ValidationReport(BaseModel):
    passed: bool
    errors: list[str] = []
    fell_back_to_template: bool = False


class Artifact(BaseModel):
    artifact_id: str
    run_id: str
    kind: ArtifactKind
    subject_id: str
    structured: dict[str, Any]  # deterministic skeleton (callers, callees, tree, paths, ...)
    mermaid: str | None = None  # call tree / sequence diagrams; sanitized
    claims: list[Claim]
    generated_by: Literal["TEMPLATE", "LLM"]
    validation: ValidationReport
    banner: str = DEFAULT_BANNER


class ReviewEvent(BaseModel):
    event_id: str
    claim_id: str
    action: ReviewAction
    reviewer: str
    rationale: str | None = None
    new_text: str | None = None  # only for EDIT
    run_id: str
    timestamp: datetime


class AskAnswer(BaseModel):
    question: str
    tool_calls: list[dict[str, Any]]  # which typed tools were used, with args
    artifact: Artifact | None
    text: str
    claims: list[Claim]
