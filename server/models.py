"""Response shapes for the API.

They are used to describe each route in the OpenAPI schema (so the web app can generate typed
code from it). The routes still return plain dicts; these models are not used to re-check them
at run time, which keeps big trees fast.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Stats(BaseModel):
    nodes: dict[str, int]
    edges: dict[str, int]


class RepoInfo(BaseModel):
    name: str
    repo_url: str | None
    commit_sha: str | None
    language: str | None
    extracted_at: str | None
    loaded_at: str | None
    stats: Stats | None
    summaries: int | None
    error: str | None


class FnBrief(BaseModel):
    id: str
    name: str
    qualified_name: str
    container: str | None
    file: str
    line_start: int
    line_end: int
    is_virtual: bool
    is_pure_virtual: bool
    is_entry_point: bool
    is_noise: bool
    noise_reason: str | None
    loc: int
    fan_in: int
    fan_out: int
    reach: int
    summary: str | None
    summary_model: str | None  # "template" when no AI was used
    summary_verified: bool | None  # None: never checked
    fields_written_count: int
    indirect_calls: int  # calls through a pointer, target unknown


class EntryPoint(FnBrief):
    rank: int


class EdgeInfo(BaseModel):
    kind: str  # "calls" or "may_call" (the first of `kinds`)
    kinds: list[str]
    count: int
    lines: list[int]
    dispatch: bool


class FnWithEdge(FnBrief, EdgeInfo):
    pass


class FieldBrief(BaseModel):
    id: str
    name: str
    qualified_name: str
    container: str | None
    type: str
    file: str
    line: int


class FieldUse(FieldBrief):
    count: int
    lines: list[int]


class SearchResponse(BaseModel):
    query: str
    functions: list[FnBrief]
    fields: list[FieldBrief]


class DataUsers(BaseModel):
    field: str
    readers: list[str]
    readers_total: int


class ImpactFacts(BaseModel):
    """What a change to the function could reach, worked out from the graph (no AI)."""

    level: Literal["low", "medium", "high"]
    reason: str
    direct_callers: int
    direct_dependents: int  # its callers + other readers of the data it changes; this sets the level
    callers_any_distance: int
    affected: int  # callers at any distance + other readers of the data it changes
    files: list[str]
    files_total: int
    is_entry_point: bool
    entry_points: list[str]  # other entry points that lead here
    entry_points_total: int
    data_users: list[DataUsers]


class SummaryInfo(BaseModel):
    short: str | None
    detailed: str | None
    model: str | None  # "template" when no AI was used
    created_at: str | None
    inputs: str | None
    outputs: str | None
    side_effects: list[str]
    mentions: list[str]
    verified: bool | None  # None: never checked
    reasons: list[str]
    role: str | None = None  # its job in the wider program (AI)
    change_risks: list[str] = []  # what could break if it changes (AI)
    business_impact: str | None = None  # what the program or its users would notice (AI)
    impact_level: Literal["low", "medium", "high"] | None = None  # from the graph alone
    impact: ImpactFacts | None = None


class IndirectCalls(BaseModel):
    count: int
    lines: list[int]


class SourceInfo(BaseModel):
    file: str
    start: int
    end: int
    code: str | None
    available: bool


class Callees(BaseModel):
    calls: list[FnWithEdge]
    may_call: list[FnWithEdge]


class ReviewFlag(BaseModel):
    code: str
    severity: Literal["review", "note"]  # "review": a person should look before relying on the explanation
    message: str  # plain English
    lines: list[int]


class FunctionDetail(BaseModel):
    node: dict[str, Any]
    summary: SummaryInfo | None
    callers: list[FnWithEdge]
    callees: Callees
    fields_read: list[FieldUse]
    fields_written: list[FieldUse]
    indirect_calls: IndirectCalls
    overrides: list[FnBrief]
    overridden_by: list[FnBrief]
    source: SourceInfo
    github_permalink: str | None
    flags: list[ReviewFlag] = []  # places the graph is incomplete, worked out from the graph alone


class TreeNode(FnBrief):
    path_id: str
    edge: EdgeInfo | None
    cycle: bool
    has_more: bool
    child_count: int
    hidden_count: int
    children: list[TreeNode]


class TreeResponse(BaseModel):
    repo: str
    direction: str
    depth: int
    include_noise: bool
    include_may_call: bool
    root: TreeNode
    node_count: int
    truncated: bool


class FieldDetail(BaseModel):
    field: dict[str, Any]
    readers: list[FnWithEdge]
    writers: list[FnWithEdge]


class ImpactFn(FnBrief):
    calls: list[str]
    via: list[str]


class ImpactLevel(BaseModel):
    distance: int
    functions: list[ImpactFn]


Severity = Literal["low", "medium", "high"]


class AffectedFn(FnBrief):
    """One function a change could reach, with how, how badly, and roughly how long to fix."""

    distance: int  # 1 = uses it directly (calls it, or reads data it changes)
    how: list[str]  # plain-English ways it is connected
    severity: Severity
    severity_reason: str
    effort_hours: float  # to update and retest it (rough)


class Effort(BaseModel):
    """Rough hours for the whole job, from the graph (a planning aid, not a quote)."""

    change: float  # making the change itself
    checks: float  # updating and retesting what it affects
    low: float
    high: float


class ImpactResponse(BaseModel):
    root: FnBrief
    max_depth: int
    total: int  # functions reached through calls (levels)
    levels: list[ImpactLevel]
    severity: Severity  # the same level the Explain tab shows
    severity_reason: str
    effort: Effort
    affected: list[AffectedFn]  # calls and data readers together, nearest and most severe first
    business_impact: str | None  # AI, from the explain step
    business_impact_checked: bool | None  # None: no AI text


class StoryResponse(BaseModel):
    function: FnBrief
    story: str
    steps: list[str]
    model: str | None
    created_at: str | None


class Rollup(BaseModel):
    key: str
    short: str | None
    detailed: str | None
    model: str | None
    created_at: str | None


class RollupDetail(Rollup):
    functions: list[FnBrief]


class StatusResponse(BaseModel):
    offline: bool  # AEROTRACE_OFFLINE=1: only saved explanations are shown, the AI is never called


class PathResponse(BaseModel):
    found: bool
    direction: str
    path: list[FnBrief]  # from the start function to the target, both included; empty when not found


class DataMapScope(BaseModel):
    kind: str  # "class" or "file"
    key: str


class DataMapAvailable(BaseModel):
    class_name: str | None
    file: str


class DataMapField(FieldBrief):
    writers: int  # how many functions in the whole repo change it
    readers: int


class DataMapLink(BaseModel):
    function_id: str
    field_id: str
    kind: str  # "reads" or "writes"
    count: int
    lines: list[int]


class DataMapResponse(BaseModel):
    scope: DataMapScope
    available: DataMapAvailable
    functions: list[FnBrief]
    fields: list[DataMapField]
    links: list[DataMapLink]
    truncated: bool
