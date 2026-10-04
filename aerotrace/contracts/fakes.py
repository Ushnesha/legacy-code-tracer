"""In-memory fakes for every cross-stream entry point (docs/CONTRACTS.md §2). LOCKED.

Each stream builds and tests against these until the real implementation is merged.
They are deterministic and use only data handed to them (no LLM, no file parsing).
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path
from typing import Literal

from aerotrace.contracts.ids import make_id
from aerotrace.contracts.models import (
    Artifact,
    ArtifactKind,
    AskAnswer,
    Claim,
    ClaimStatus,
    Edge,
    EdgeType,
    Evidence,
    FileCoverage,
    ImpactItem,
    ImpactResult,
    Node,
    NodeKind,
    NormalizedCodeModel,
    PathHop,
    ReviewEvent,
    RunManifest,
    TraversalLimits,
    ValidationReport,
)

SAMPLE_NCM_PATH = Path(__file__).resolve().parent / "sample_ncm.json"

EMPTY_IMPACT_MESSAGE = (
    "No additional impacts were found within the analyzed scope. Review coverage, exclusions, "
    "variant and unresolved dependencies before relying on this result."
)

_UNCERTAIN_EDGES: frozenset[str] = frozenset({"MAY_CALL", "UNRESOLVED_CALL"})
_IMPACT_EDGES: list[EdgeType] = ["CALLS", "MAY_CALL", "UNRESOLVED_CALL", "READS", "WRITES"]


def load_sample_ncm() -> NormalizedCodeModel:
    """Load and validate ``aerotrace/contracts/sample_ncm.json``.

    Raises FileNotFoundError if the sample has not been generated, or pydantic.ValidationError
    if it no longer matches the contracts.
    """
    return NormalizedCodeModel.model_validate_json(SAMPLE_NCM_PATH.read_text(encoding="utf-8"))


# --- analysis ---------------------------------------------------------------------------------


def fake_run_analysis(
    repo_path: Path, variant: str = "baseline", compile_db: Path | None = None
) -> NormalizedCodeModel:
    """Fake of ``aerotrace.analysis.run_analysis``: return the sample NCM.

    ``repo_path`` is recorded in the manifest but never read. A non-baseline ``variant`` gets its
    own run_id (derived from the sample run_id and the variant) so runs never collide.
    ``compile_db`` is ignored.
    """
    ncm = load_sample_ncm()
    manifest = ncm.manifest.model_copy(update={"repo_path": str(repo_path)})
    if variant != manifest.variant:
        manifest = manifest.model_copy(
            update={"variant": variant, "run_id": make_id(manifest.run_id, "variant", variant)}
        )
    return ncm.model_copy(update={"manifest": manifest})


# --- graph ------------------------------------------------------------------------------------


class _Run:
    """Indexes over one loaded NCM."""

    def __init__(self, ncm: NormalizedCodeModel) -> None:
        self.ncm = ncm
        self.nodes: dict[str, Node] = {n.id: n for n in ncm.nodes}
        self.evidence: dict[str, Evidence] = {e.evidence_id: e for e in ncm.evidence}
        self.out_edges: dict[str, list[Edge]] = {}
        self.in_edges: dict[str, list[Edge]] = {}
        for edge in ncm.edges:
            self.out_edges.setdefault(edge.source, []).append(edge)
            self.in_edges.setdefault(edge.target, []).append(edge)

    def validate(self) -> list[str]:
        errors: list[str] = []
        for node in self.ncm.nodes:
            for ev_id in node.evidence_ids:
                if ev_id not in self.evidence:
                    errors.append(f"node {node.name}: missing evidence {ev_id}")
        for edge in self.ncm.edges:
            if not edge.evidence_ids:
                errors.append(f"edge {edge.type} {edge.source}->{edge.target}: no evidence")
            for ev_id in edge.evidence_ids:
                if ev_id not in self.evidence:
                    errors.append(f"edge {edge.type}: missing evidence {ev_id}")
            for end in (edge.source, edge.target):
                if end not in self.nodes:
                    errors.append(f"edge {edge.type}: unknown node {end}")
        return errors


def _sorted_edges(edges: list[Edge]) -> list[Edge]:
    """Stable order: by type, then call-site ``order`` property (if any), then ids."""

    def key(e: Edge) -> tuple[str, int, str, str]:
        order = e.properties.get("order")
        return (e.type, order if isinstance(order, int) else 0, e.target, e.source)

    return sorted(edges, key=key)


class FakeGraphRepository:
    """In-memory implementation of the full ``GraphRepository`` protocol.

    Not persistent. Unknown run_ids raise KeyError; lookups of unknown nodes, evidence,
    artifacts or claims return None.
    """

    def __init__(self) -> None:
        self._runs: dict[str, _Run] = {}
        self._active: str | None = None
        self._artifacts: dict[str, Artifact] = {}
        self._claims: dict[str, Claim] = {}
        self._reviews: list[ReviewEvent] = []

    def _run(self, run_id: str) -> _Run:
        try:
            return self._runs[run_id]
        except KeyError:
            raise KeyError(f"unknown run_id: {run_id}") from None

    # build

    def load_model(self, ncm: NormalizedCodeModel) -> str:
        """Stage ``ncm`` (not yet active) and return its run_id. Re-loading replaces it."""
        run_id = ncm.manifest.run_id
        self._runs[run_id] = _Run(ncm)
        return run_id

    def activate(self, run_id: str) -> None:
        """Validate a staged run and make it active. Raises ValueError listing problems."""
        errors = self._run(run_id).validate()
        if errors:
            raise ValueError(f"run {run_id} failed validation: " + "; ".join(errors))
        self._active = run_id

    def active_run_id(self) -> str | None:
        """Return the active run_id, or None if nothing was activated."""
        return self._active

    def get_manifest(self, run_id: str) -> RunManifest:
        """Return the run's manifest. Raises KeyError for an unknown run."""
        return self._run(run_id).ncm.manifest

    def get_coverage(self, run_id: str) -> list[FileCoverage]:
        """Return per-file parse coverage for the run."""
        return list(self._run(run_id).ncm.coverage)

    def get_known_gaps(self, run_id: str) -> list[str]:
        """Return human-readable analysis gaps for the run."""
        return list(self._run(run_id).ncm.known_gaps)

    # read

    def get_node(self, run_id: str, node_id: str) -> Node | None:
        """Return the node, or None if it is not in the run."""
        return self._run(run_id).nodes.get(node_id)

    def find_symbols(
        self, run_id: str, query: str, kinds: list[NodeKind] | None = None, limit: int = 20
    ) -> list[Node]:
        """Case-insensitive substring search on name / qualified_name.

        Exact name matches come first, then alphabetical. Returns at most ``limit`` nodes.
        """
        q = query.strip().lower()
        if not q:
            return []
        hits = [
            n
            for n in self._run(run_id).nodes.values()
            if (kinds is None or n.kind in kinds)
            and (q in n.name.lower() or q in n.qualified_name.lower())
        ]
        hits.sort(key=lambda n: (n.name.lower() != q, n.name, n.id))
        return hits[: max(limit, 0)]

    def edges_from(
        self, run_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]:
        """Outgoing edges of ``node_id``, optionally filtered by type, in stable order."""
        edges = self._run(run_id).out_edges.get(node_id, [])
        return _sorted_edges([e for e in edges if types is None or e.type in types])

    def edges_to(
        self, run_id: str, node_id: str, types: list[EdgeType] | None = None
    ) -> list[Edge]:
        """Incoming edges of ``node_id``, optionally filtered by type, in stable order."""
        edges = self._run(run_id).in_edges.get(node_id, [])
        return _sorted_edges([e for e in edges if types is None or e.type in types])

    def get_evidence(self, run_id: str, evidence_id: str) -> Evidence | None:
        """Return the evidence span, or None if it is not in the run."""
        return self._run(run_id).evidence.get(evidence_id)

    def bounded_traversal(
        self,
        run_id: str,
        seed_id: str,
        types: list[EdgeType],
        direction: Literal["out", "in"],
        limits: TraversalLimits | None = None,
    ) -> tuple[list[list[PathHop]], bool, str | None]:
        """BFS from ``seed_id`` along ``types`` edges.

        ``direction="out"`` follows source->target, ``"in"`` follows target->source; hops always
        keep the edge's real source/target. Returns one shortest path per reached node (seed
        excluded), sorted by (length, node ids), plus (truncated, truncation_reason).
        """
        limits = limits or TraversalLimits()
        run = self._run(run_id)
        if seed_id not in run.nodes:
            return [], False, None
        deadline = time.monotonic() + limits.timeout_s
        paths: dict[str, list[PathHop]] = {seed_id: []}
        queue: deque[str] = deque([seed_id])
        edges_seen = 0
        reason: str | None = None

        while queue and reason is None:
            current = queue.popleft()
            depth = len(paths[current])
            if depth >= limits.max_depth:
                if self._has_neighbours(run_id, current, types, direction):
                    reason = f"max_depth {limits.max_depth} reached"
                continue
            if direction == "out":
                edges = self.edges_from(run_id, current, types)
            else:
                edges = self.edges_to(run_id, current, types)
            for edge in edges:
                if time.monotonic() > deadline:
                    reason = f"timeout {limits.timeout_s}s reached"
                    break
                edges_seen += 1
                if edges_seen > limits.max_edges:
                    reason = f"max_edges {limits.max_edges} reached"
                    break
                nxt = edge.target if direction == "out" else edge.source
                if nxt in paths:
                    continue
                if len(paths) - 1 >= limits.max_nodes:
                    reason = f"max_nodes {limits.max_nodes} reached"
                    break
                hop = PathHop(
                    source=edge.source,
                    target=edge.target,
                    edge_type=edge.type,
                    evidence_ids=list(edge.evidence_ids),
                )
                paths[nxt] = [*paths[current], hop]
                queue.append(nxt)

        result = [p for node_id, p in paths.items() if node_id != seed_id]
        result.sort(key=lambda p: (len(p), [(h.source, h.target, h.edge_type) for h in p]))
        return result, reason is not None, reason

    def _has_neighbours(
        self, run_id: str, node_id: str, types: list[EdgeType], direction: Literal["out", "in"]
    ) -> bool:
        if direction == "out":
            return bool(self.edges_from(run_id, node_id, types))
        return bool(self.edges_to(run_id, node_id, types))

    # claims and review

    def save_artifact(self, artifact: Artifact) -> None:
        """Store (or replace) an artifact and upsert its claims."""
        self._artifacts[artifact.artifact_id] = artifact
        for claim in artifact.claims:
            self.save_claim(claim)

    def get_artifact(self, artifact_id: str) -> Artifact | None:
        """Return the artifact, or None."""
        return self._artifacts.get(artifact_id)

    def save_claim(self, claim: Claim) -> None:
        """Store (or replace) a claim by claim_id."""
        self._claims[claim.claim_id] = claim

    def get_claim(self, claim_id: str) -> Claim | None:
        """Return the claim, or None."""
        return self._claims.get(claim_id)

    def list_claims(self, run_id: str, status: ClaimStatus | None = None) -> list[Claim]:
        """Claims for ``run_id`` (optionally one status), sorted by claim_id."""
        claims = [
            c
            for c in self._claims.values()
            if c.run_id == run_id and (status is None or c.status == status)
        ]
        return sorted(claims, key=lambda c: c.claim_id)

    def save_review(self, event: ReviewEvent) -> None:
        """Append a review event. Never edits or deletes earlier events or the claim itself."""
        self._reviews.append(event)

    def list_reviews(self, claim_id: str) -> list[ReviewEvent]:
        """Review events for a claim, oldest first."""
        events = [e for e in self._reviews if e.claim_id == claim_id]
        return sorted(events, key=lambda e: e.timestamp)

    # staleness

    def mark_stale_for_changed_evidence(self, old_run_id: str, new_run_id: str) -> list[str]:
        """Mark ACCEPTED/EDITED claims of ``old_run_id`` STALE if cited evidence changed.

        Cited evidence counts as unchanged only if the new run has evidence in the same file with
        the same snippet_hash (so pure line shifts are not stale). Missing evidence counts as
        changed. Returns the sorted claim_ids that were marked STALE.
        """
        old = self._run(old_run_id)
        new = self._run(new_run_id)
        new_hashes: set[tuple[str, str]] = {(e.file, e.snippet_hash) for e in new.ncm.evidence}
        stale: list[str] = []
        for claim in self.list_claims(old_run_id):
            if claim.status not in ("ACCEPTED", "EDITED"):
                continue
            for ev_id in claim.evidence_ids:
                ev = old.evidence.get(ev_id)
                if ev is None or (ev.file, ev.snippet_hash) not in new_hashes:
                    self._claims[claim.claim_id] = claim.model_copy(update={"status": "STALE"})
                    stale.append(claim.claim_id)
                    break
        return sorted(stale)


# --- impact -----------------------------------------------------------------------------------


def _path_is_uncertain(repo: FakeGraphRepository, run_id: str, path: list[PathHop]) -> bool:
    for hop in path:
        if hop.edge_type in _UNCERTAIN_EDGES:
            return True
        for ev_id in hop.evidence_ids:
            ev = repo.get_evidence(run_id, ev_id)
            if ev is None or ev.quality == "FALLBACK_PARSED":
                return True
    return False


def _reason(path: list[PathHop], names: dict[str, str]) -> str:
    hop = path[-1]
    verb = {
        "CALLS": "calls",
        "MAY_CALL": "may call",
        "UNRESOLVED_CALL": "has an unresolved call to",
        "READS": "reads",
        "WRITES": "writes",
        "VERIFIES": "verifies",
    }.get(hop.edge_type, hop.edge_type.lower())
    text = f"{names.get(hop.source, hop.source)} {verb} {names.get(hop.target, hop.target)}"
    if len(path) > 1:
        text += f" ({len(path)} hops from seed)"
    return text


def fake_compute_impact(
    repo: FakeGraphRepository,
    run_id: str,
    seed_ids: list[str],
    limits: TraversalLimits | None = None,
) -> ImpactResult:
    """Fake of ``aerotrace.impact.compute_impact`` using ``bounded_traversal``.

    Walks CALLS / MAY_CALL / UNRESOLVED_CALL / READS / WRITES edges backwards from each seed.
    Depth-1 nodes are DIRECT, deeper ones TRANSITIVE; any path through MAY_CALL,
    UNRESOLVED_CALL or fallback-parsed evidence goes to UNCERTAIN_FRONTIER instead. Tests that
    VERIFY a seed or an impacted node go to VERIFICATION. Unknown seeds are skipped.
    """
    direct: dict[str, ImpactItem] = {}
    transitive: dict[str, ImpactItem] = {}
    frontier: dict[str, ImpactItem] = {}
    verification: dict[str, ImpactItem] = {}
    truncated = False
    reasons: list[str] = []
    names: dict[str, str] = {}

    def name_of(node_id: str) -> str:
        if node_id not in names:
            node = repo.get_node(run_id, node_id)
            names[node_id] = node.name if node else node_id
        return names[node_id]

    seeds = [s for s in seed_ids if repo.get_node(run_id, s) is not None]
    for seed in seeds:
        paths, was_truncated, why = repo.bounded_traversal(
            run_id, seed, _IMPACT_EDGES, "in", limits
        )
        if was_truncated:
            truncated = True
            if why and why not in reasons:
                reasons.append(why)
        for path in paths:
            for hop in path:
                name_of(hop.source)
                name_of(hop.target)
            node_id = path[-1].source
            if node_id in seeds:
                continue
            item = ImpactItem(
                node_id=node_id, name=name_of(node_id), path=path, reason=_reason(path, names)
            )
            if _path_is_uncertain(repo, run_id, path):
                frontier.setdefault(node_id, item)
            elif len(path) == 1:
                direct.setdefault(node_id, item)
            else:
                transitive.setdefault(node_id, item)

    for groups in (direct, frontier):
        for node_id in list(groups):
            transitive.pop(node_id, None)
    for node_id in direct:
        frontier.pop(node_id, None)

    impacted = [*seeds, *direct, *transitive, *frontier]
    base_paths = {i.node_id: i.path for g in (direct, transitive, frontier) for i in g.values()}
    for target in impacted:
        for edge in repo.edges_to(run_id, target, ["VERIFIES"]):
            if edge.source in verification:
                continue
            hop = PathHop(
                source=edge.source,
                target=edge.target,
                edge_type="VERIFIES",
                evidence_ids=list(edge.evidence_ids),
            )
            path = [*base_paths.get(target, []), hop]
            name_of(edge.source)
            name_of(edge.target)
            verification[edge.source] = ImpactItem(
                node_id=edge.source,
                name=name_of(edge.source),
                path=path,
                reason=f"{name_of(edge.source)} verifies {name_of(edge.target)}",
            )

    def ordered(group: dict[str, ImpactItem]) -> list[ImpactItem]:
        return sorted(group.values(), key=lambda i: (len(i.path), i.name, i.node_id))

    result = ImpactResult(
        run_id=run_id,
        seed_ids=list(seed_ids),
        direct=ordered(direct),
        transitive=ordered(transitive),
        verification=ordered(verification),
        uncertain_frontier=ordered(frontier),
        truncated=truncated,
        truncation_reason="; ".join(reasons) or None,
    )
    if not (result.direct or result.transitive or result.verification or result.uncertain_frontier):
        result.empty_message = EMPTY_IMPACT_MESSAGE
    return result


# --- narrative --------------------------------------------------------------------------------


def _mermaid_label(text: str) -> str:
    return "".join(ch for ch in text if ch.isalnum() or ch in "_ .-:")


def fake_generate_artifact(
    repo: FakeGraphRepository,
    run_id: str,
    kind: ArtifactKind,
    subject_id: str,
    use_llm: bool = True,
) -> Artifact:
    """Fake of ``aerotrace.narrative.generate_artifact``: template-only, never calls an LLM.

    The structured skeleton lists callers, callees, reads and writes. Emits 1-2 claims, each
    citing real evidence from the run: SUPPORTED if that evidence is PRECISE, else INFERRED.
    ``use_llm`` is ignored. Raises ValueError if ``subject_id`` is not in the run.
    """
    node = repo.get_node(run_id, subject_id)
    if node is None:
        raise ValueError(f"subject {subject_id} not found in run {run_id}")

    def names(edges: list[Edge], attr: Literal["source", "target"]) -> list[str]:
        out: list[str] = []
        for e in edges:
            other = repo.get_node(run_id, getattr(e, attr))
            out.append(other.name if other else getattr(e, attr))
        return out

    call_types: list[EdgeType] = ["CALLS", "MAY_CALL", "UNRESOLVED_CALL"]
    callee_edges = repo.edges_from(run_id, subject_id, call_types)
    caller_edges = repo.edges_to(run_id, subject_id, call_types)
    structured = {
        "subject": {
            "id": node.id,
            "name": node.name,
            "kind": node.kind,
            "signature": node.signature,
            "file": node.file,
        },
        "callers": names(caller_edges, "source"),
        "callees": names(callee_edges, "target"),
        "reads": names(repo.edges_from(run_id, subject_id, ["READS"]), "target"),
        "writes": names(repo.edges_from(run_id, subject_id, ["WRITES"]), "target"),
        "known_gaps": repo.get_known_gaps(run_id),
    }

    mermaid: str | None = None
    if kind in ("call_tree", "sequence"):
        lines = ["graph TD"]
        for e, callee in zip(callee_edges, structured["callees"], strict=True):
            arrow = "-->" if e.type == "CALLS" else "-.->"
            lines.append(
                f'    n0["{_mermaid_label(node.name)}"] {arrow} '
                f'n{len(lines)}["{_mermaid_label(callee)}"]'
            )
        mermaid = "\n".join(lines)

    artifact_id = make_id("artifact", run_id, kind, subject_id)
    candidates: list[tuple[str, list[str]]] = []
    if node.evidence_ids:
        where = f"{node.file}:{node.start_line}" if node.start_line else (node.file or "the run")
        candidates.append((f"{node.name} is defined at {where}.", node.evidence_ids[:1]))
    if callee_edges and callee_edges[0].type == "CALLS":
        candidates.append(
            (f"{node.name} calls {structured['callees'][0]}.", callee_edges[0].evidence_ids[:1])
        )

    claims: list[Claim] = []
    for text, ev_ids in candidates[:2]:
        ev = repo.get_evidence(run_id, ev_ids[0])
        if ev is None:
            continue
        claims.append(
            Claim(
                claim_id=make_id(artifact_id, subject_id, str(len(claims))),
                run_id=run_id,
                artifact_id=artifact_id,
                subject_id=subject_id,
                text=text,
                classification="SUPPORTED" if ev.quality == "PRECISE" else "INFERRED",
                evidence_ids=ev_ids,
                origin="TEMPLATE",
            )
        )

    return Artifact(
        artifact_id=artifact_id,
        run_id=run_id,
        kind=kind,
        subject_id=subject_id,
        structured=structured,
        mermaid=mermaid,
        claims=claims,
        generated_by="TEMPLATE",
        validation=ValidationReport(passed=True),
    )


def fake_answer(repo: FakeGraphRepository, run_id: str, question: str) -> AskAnswer:
    """Fake of ``aerotrace.ask.answer``: find the first function named in the question and
    return its template function card. Never calls an LLM. If no function matches, returns an
    answer with no artifact and no claims.
    """
    tool_calls: list[dict] = []
    words = [w.strip("?.,!:;()'\"") for w in question.split()]
    for word in words:
        if len(word) < 3:
            continue
        tool_calls.append({"tool": "find_symbols", "args": {"query": word, "kinds": ["Function"]}})
        hits = repo.find_symbols(run_id, word, kinds=["Function"], limit=1)
        if hits and hits[0].name.lower() == word.lower():
            node = hits[0]
            tool_calls.append(
                {
                    "tool": "generate_artifact",
                    "args": {"kind": "function_card", "subject_id": node.id},
                }
            )
            artifact = fake_generate_artifact(repo, run_id, "function_card", node.id, False)
            text = " ".join(c.text for c in artifact.claims) or f"Found {node.name}."
            return AskAnswer(
                question=question,
                tool_calls=tool_calls,
                artifact=artifact,
                text=text,
                claims=artifact.claims,
            )
    return AskAnswer(
        question=question,
        tool_calls=tool_calls,
        artifact=None,
        text="No matching function was found in the analyzed scope.",
        claims=[],
    )
