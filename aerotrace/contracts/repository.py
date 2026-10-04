"""GraphRepository protocol (docs/CONTRACTS.md §6). LOCKED."""

from __future__ import annotations

from typing import Literal, Protocol

from aerotrace.contracts.models import (
    Artifact,
    Claim,
    ClaimStatus,
    Edge,
    EdgeType,
    Evidence,
    FileCoverage,
    Node,
    NodeKind,
    NormalizedCodeModel,
    PathHop,
    ReviewEvent,
    RunManifest,
    TraversalLimits,
)


class GraphRepository(Protocol):
    # build
    def load_model(self, ncm: NormalizedCodeModel) -> str: ...  # stages it, returns run_id
    def activate(self, run_id: str) -> None: ...  # validate then make active
    def active_run_id(self) -> str | None: ...
    def get_manifest(self, run_id: str) -> RunManifest: ...
    def get_coverage(self, run_id: str) -> list[FileCoverage]: ...
    def get_known_gaps(self, run_id: str) -> list[str]: ...

    # read
    def get_node(self, run_id: str, node_id: str) -> Node | None: ...
    def find_symbols(
        self, run_id: str, query: str, kinds: list[NodeKind] | None = None, limit: int = 20
    ) -> list[Node]: ...
    def edges_from(
        self, run_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]: ...
    def edges_to(
        self, run_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]: ...
    def get_evidence(self, run_id: str, evidence_id: str) -> Evidence | None: ...
    def bounded_traversal(
        self,
        run_id: str,
        seed_id: str,
        types: list[EdgeType],
        direction: Literal["out", "in"],
        limits: TraversalLimits | None = None,
    ) -> tuple[list[list[PathHop]], bool, str | None]: ...

    #   returns (paths, truncated, truncation_reason)

    # claims and review
    def save_artifact(self, artifact: Artifact) -> None: ...
    def get_artifact(self, artifact_id: str) -> Artifact | None: ...
    def save_claim(self, claim: Claim) -> None: ...
    def get_claim(self, claim_id: str) -> Claim | None: ...
    def list_claims(self, run_id: str, status: ClaimStatus | None = None) -> list[Claim]: ...
    def save_review(self, event: ReviewEvent) -> None: ...  # append-only
    def list_reviews(self, claim_id: str) -> list[ReviewEvent]: ...

    # staleness
    def mark_stale_for_changed_evidence(self, old_run_id: str, new_run_id: str) -> list[str]: ...

    #   claims that were ACCEPTED/EDITED and whose cited evidence hash changed -> STALE;
    #   returns claim_ids
