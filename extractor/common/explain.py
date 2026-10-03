"""LLM summaries for every function, written bottom-up and checked against the graph.

    python -m extractor.common.explain <repo> [--only-reachable-from-entry-points] [--max N]
                                       [--concurrency 8] [--yes]

Works on data/<repo>.db (load the graph first: python -m server.store load ...).
  1. Callees are summarized before their callers (Tarjan SCCs, condensed to a DAG, level by
     level; functions in the same cycle are summarized together in one call).
  2. Noise functions and bodiless virtual methods get a template sentence built from the graph.
  3. Every answer is checked: names it mentions must exist and be one hop away, and fields in
     its side effects must really be written by the function. One retry with the errors fed
     back; if still wrong the summary is kept but flagged verified=false.
  4. Results are cached by hash (source + callee summaries + prompt version + model).
  5. Afterwards: class and file roll-ups, and a "what happens when it runs" story per entry point.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from server.store import DATA_DIR, connect, migrate, rebuild_fts, resolve_repo_dir

from .enrich import _sccs

PROMPT_VERSION = "1"
DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_FAST_MODEL = "claude-haiku-4-5-20251001"
FAST_MODEL_MAX_LOC = 15  # functions shorter than this use the fast model
MAX_CONTEXT_CHARS = 24_000  # ~6k tokens at ~4 characters per token
MAX_CALLEES_SHOWN = 25
MAX_FIELDS_SHOWN = 30
MAX_CALLERS_SHOWN = 5
MAX_CYCLE_CHUNK = 6  # a bigger cycle is summarized in sequential chunks of this size
STORY_DEPTH = 3
STORY_MAX_NODES = 80
STORY_MAX_CHILDREN = 12

# $ per million tokens (input, output) for the cost estimate; unknown models just show tokens.
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
EXPECTED_OUTPUT_TOKENS = 450  # per function, for the estimate

SYSTEM_PROMPT = """You explain code from a legacy C/C++ codebase to engineers who are new to it.

Rules:
1. Explain WHAT the function does and WHY it matters to the program. Do not narrate the code line by line.
2. Use plain, everyday English. No jargon. Short sentences.
3. Never guess about code you were not shown. If something is not clear from the code and the context given, say "unclear from the code".
4. "mentions" lists every function and field name you used in your text. Only list names that appear in the "Calls", "Reads", "Writes" or "Called by" lists you were given, written exactly as shown there. Do not list library functions, class names or anything else; describe those in plain words instead.
5. "side_effects" lists the state the function changes. When the change is to a field, write that field's exact name. Only name fields from the "Writes" list. Use an empty list if nothing is changed.
6. Answer with a single JSON object and nothing else: no markdown fences, no commentary."""

ANSWER_SHAPE = """{
  "short": "one plain-English sentence, at most 20 words",
  "detailed": "3 to 5 short plain-English sentences for a new engineer",
  "inputs": "what it takes in",
  "outputs": "what it returns",
  "side_effects": ["state it changes", "..."],
  "mentions": ["every function and field name used in the text above"]
}"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------- LLM access


@dataclass
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLM(Protocol):
    async def complete(self, *, model: str, system: str, messages: list[dict], max_tokens: int) -> LLMResult: ...


class AnthropicLLM:
    """The real thing: the Anthropic SDK's async client."""

    def __init__(self, client=None):
        if client is None:
            import anthropic

            client = anthropic.AsyncAnthropic()
        self.client = client

    async def complete(self, *, model: str, system: str, messages: list[dict], max_tokens: int) -> LLMResult:
        kwargs: dict = dict(model=model, max_tokens=max_tokens, system=system, messages=messages)
        effort = os.environ.get("AEROTRACE_EFFORT", "low")  # summaries do not need deep thinking
        if effort and "haiku" not in model:  # Haiku 4.5 does not accept `effort`
            kwargs["output_config"] = {"effort": effort}
        resp = await self.client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text")
        usage = getattr(resp, "usage", None)
        return LLMResult(text, getattr(usage, "input_tokens", 0) or 0, getattr(usage, "output_tokens", 0) or 0)


# ---------------------------------------------------------------- the graph, read from SQLite


@dataclass
class Fn:
    id: str
    name: str
    qname: str
    container: str | None
    file: str
    start: int
    end: int
    loc: int
    signature: str
    doc: str | None
    is_noise: bool
    noise_reason: str | None
    is_pure_virtual: bool
    is_virtual: bool
    external_calls: int
    indirect_lines: list[int]
    reach: int

    @property
    def cls(self) -> str | None:
        if self.container is None or "::" not in self.qname:
            return None
        return self.qname.rsplit("::", 1)[0]

    @property
    def templated(self) -> bool:
        """Gets a sentence built from the graph, no LLM call."""
        return self.is_noise or self.is_pure_virtual


@dataclass
class Fld:
    id: str
    name: str
    qname: str
    type: str
    container: str | None


class GraphView:
    def __init__(self, conn: sqlite3.Connection):
        self.fns: dict[str, Fn] = {}
        self.fields: dict[str, Fld] = {}
        self.callees: dict[str, dict[str, dict]] = {}  # fn -> callee -> {"kinds": set, "lines": [..]}
        self.callers: dict[str, set[str]] = {}
        self.reads: dict[str, dict[str, list[int]]] = {}
        self.writes: dict[str, dict[str, list[int]]] = {}
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        self.repo_name = meta.get("repo_name") or ""
        self.repo_dir = resolve_repo_dir(meta.get("repo_dir"), self.repo_name)
        self.entry_points: list[str] = json.loads(meta.get("entry_points") or "[]")
        self.class_bases: dict[str, list[str]] = json.loads(meta.get("class_bases") or "{}")
        self.class_docs: dict[str, str] = json.loads(meta.get("class_docs") or "{}")

        for r in conn.execute("SELECT * FROM functions"):
            node = json.loads(r["node_json"])
            self.fns[r["id"]] = Fn(
                id=r["id"], name=r["name"], qname=r["qualified_name"], container=r["container"],
                file=r["file"], start=r["line_start"], end=r["line_end"], loc=r["loc"],
                signature=node.get("signature", r["name"]), doc=node.get("doc_comment"),
                is_noise=bool(r["is_noise"]), noise_reason=r["noise_reason"],
                is_pure_virtual=bool(r["is_pure_virtual"]), is_virtual=bool(r["is_virtual"]),
                external_calls=node.get("external_calls", 0), indirect_lines=node.get("indirect_calls", []),
                reach=r["reach"],
            )
        for r in conn.execute("SELECT id, name, qualified_name, type, container FROM fields"):
            self.fields[r["id"]] = Fld(r["id"], r["name"], r["qualified_name"], r["type"], r["container"])
        for r in conn.execute("SELECT source, target, kind, lines_json FROM edges WHERE kind IN ('calls','may_call','reads','writes')"):
            s, t, k = r["source"], r["target"], r["kind"]
            lines = json.loads(r["lines_json"])
            if k in ("reads", "writes"):
                if s in self.fns and t in self.fields:
                    (self.reads if k == "reads" else self.writes).setdefault(s, {}).setdefault(t, []).extend(lines)
            elif s in self.fns and t in self.fns and s != t:
                e = self.callees.setdefault(s, {}).setdefault(t, {"kinds": set(), "lines": []})
                e["kinds"].add(k)
                e["lines"].extend(lines)
                self.callers.setdefault(t, set()).add(s)
        self._by_name: dict[str, list[str]] = {}
        for i, f in self.fns.items():
            self._by_name.setdefault(f.name, []).append(i)

    def source(self, fn: Fn) -> str | None:
        """Only this function's lines, read from the repo checkout."""
        if not self.repo_dir:
            return None
        root = Path(self.repo_dir).resolve()
        path = (root / fn.file).resolve()
        if root not in path.parents or not path.is_file():
            return None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[max(fn.start - 1, 0) : fn.end])

    def reachable_from_entry_points(self) -> set[str]:
        seen: set[str] = set()
        todo = [i for i in self.entry_points if i in self.fns]
        while todo:
            x = todo.pop()
            if x in seen:
                continue
            seen.add(x)
            todo.extend(self.callees.get(x, {}))
        return seen


# ---------------------------------------------------------------- 1. ordering


@dataclass
class Plan:
    levels: list[list[list[str]]]  # level -> groups -> function ids (a group is one cycle or one function)

    @property
    def groups(self) -> list[list[str]]:
        return [g for lvl in self.levels for g in lvl]


def make_plan(view: GraphView, targets: set[str]) -> Plan:
    """Tarjan SCCs over calls + may_call among `targets`, condensed, callees first."""
    ids = sorted(targets, key=lambda i: (view.fns[i].qname, i))
    idx = {i: n for n, i in enumerate(ids)}
    succ = [{idx[c] for c in view.callees.get(i, {}) if c in idx and c != i} for i in ids]
    comps = _sccs(len(ids), succ)  # sinks first: every callee component comes before its callers
    comp_of = {}
    for c, members in enumerate(comps):
        for m in members:
            comp_of[m] = c
    level = [0] * len(comps)
    for c, members in enumerate(comps):
        deps = {comp_of[w] for m in members for w in succ[m] if comp_of[w] != c}
        level[c] = 1 + max((level[d] for d in deps), default=-1)
    levels: list[list[list[str]]] = [[] for _ in range(max(level, default=-1) + 1)]
    for c, members in enumerate(comps):
        levels[level[c]].append(sorted((ids[m] for m in members), key=lambda i: (view.fns[i].qname, i)))
    for lvl in levels:
        lvl.sort(key=lambda g: (view.fns[g[0]].qname, g[0]))
    return Plan(levels)


# ---------------------------------------------------------------- templates (no LLM)


def template_summary(view: GraphView, fn: Fn) -> dict:
    reads = [view.fields[f] for f in view.reads.get(fn.id, {})]
    writes = [view.fields[f] for f in view.writes.get(fn.id, {})]
    cls = fn.container or "its class"
    if fn.is_pure_virtual:
        short = f"A method every subclass of {cls} must provide; what it does depends on the subclass."
    elif fn.noise_reason == "operator":
        op = fn.name[len("operator"):].strip()
        short = (
            f"Defines what the {op} operator does for {cls}." if op and not op[0].isalpha()
            else f"Converts a {cls} to {op or 'another type'}."
        )
    elif fn.noise_reason == "trivial_constructor":
        short = f"Creates a new {cls} with its fields set to their starting values."
    elif fn.noise_reason == "trivial_destructor":
        short = f"Cleans up a {cls} when it is destroyed; nothing special happens."
    elif fn.noise_reason == "accessor" and len(reads) + len(writes) == 1 and not writes:
        short = f"Returns the value of {reads[0].name}."
    elif fn.noise_reason == "accessor" and len(writes) == 1 and not reads:
        short = f"Sets {writes[0].name} to the value it is given."
    elif fn.noise_reason == "accessor":
        f1 = (reads or writes)[0]
        short = f"Reads and updates {f1.name}."
    elif fn.noise_reason == "noise_class":
        short = f"Support code in {cls}; not summarized in detail because it is a helper, not part of the main story."
    else:
        short = f"Support code ({fn.name}); not summarized in detail because it is a helper, not part of the main story."
    extra = []
    if reads:
        extra.append("It reads " + ", ".join(sorted(f.name for f in reads)) + ".")
    if writes:
        extra.append("It changes " + ", ".join(sorted(f.name for f in writes)) + ".")
    return {
        "short": short,
        "detailed": " ".join([short, *extra]),
        "side_effects": sorted(f.name for f in writes),
        "mentions": sorted({f.name for f in reads + writes} if fn.noise_reason == "accessor" else set()),
    }


# ---------------------------------------------------------------- 2. context


def trim_middle(text: str, budget: int) -> str:
    if len(text) <= budget:
        return text
    head, tail = int(budget * 0.6), int(budget * 0.4)
    cut = text[head : len(text) - tail].count("\n") + 1
    return f"{text[:head]}\n... [{cut} lines trimmed from the middle] ...\n{text[len(text) - tail:]}"


def clip(text: str | None, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def build_function_section(
    view: GraphView, fn: Fn, summaries: dict[str, str], budget: int, pending: set[str]
) -> tuple[str, list]:
    """One function's context. Returns (text, hash_parts): the parts that decide the cache key."""
    cls = fn.cls
    callees = sorted(
        view.callees.get(fn.id, {}).items(),
        key=lambda kv: ("calls" not in kv[1]["kinds"], min(kv[1]["lines"] or [0]), view.fns[kv[0]].qname),
    )
    callee_lines, callee_parts = [], []
    for cid, info in callees[:MAX_CALLEES_SHOWN]:
        c = view.fns[cid]
        s = None if cid in pending else summaries.get(cid)  # cycle-mates are explained in the same call
        what = clip(s, 200) if s else ("(explained in the same answer)" if cid in pending else "(no summary available)")
        note = " [a virtual call: one of several functions it could reach]" if info["kinds"] == {"may_call"} else ""
        callee_lines.append(f"- {c.qname}: {what}{note}")
        callee_parts.append([c.qname, s])
    if len(callees) > MAX_CALLEES_SHOWN:
        callee_lines.append(f"- (and {len(callees) - MAX_CALLEES_SHOWN} more)")
    reads = [view.fields[f] for f in sorted(view.reads.get(fn.id, {}), key=lambda f: view.fields[f].qname)]
    writes = [view.fields[f] for f in sorted(view.writes.get(fn.id, {}), key=lambda f: view.fields[f].qname)]
    fmt = lambda fs: [f"- {f.qname} ({f.type})" for f in fs[:MAX_FIELDS_SHOWN]] or ["- (none)"]
    callers = sorted(view.callers.get(fn.id, ()), key=lambda c: (view.fns[c].is_noise, view.fns[c].qname))
    caller_names = [view.fns[c].qname for c in callers[:MAX_CALLERS_SHOWN]]
    if len(callers) > MAX_CALLERS_SHOWN:
        caller_names.append(f"(and {len(callers) - MAX_CALLERS_SHOWN} more)")
    extra = []
    if fn.external_calls:
        extra.append(f"It also makes {fn.external_calls} call(s) to code outside this project (library code).")
    if fn.indirect_lines:
        extra.append(
            f"It makes {len(fn.indirect_lines)} call(s) through a pointer or callback "
            f"(lines {', '.join(map(str, fn.indirect_lines[:10]))}); what they call is unknown."
        )

    class_doc = view.class_docs.get(cls or "")
    head = [
        f"### Function: {fn.qname}   (id: {fn.id})",
        f"File: {fn.file} lines {fn.start}-{fn.end}",
        f"Class: {cls or '(none, free function)'}",
        f"Signature: {fn.signature}",
    ]
    if fn.doc:
        head.append(f"Doc comment: {clip(fn.doc, 1500)}")
    if class_doc:
        head.append(f"Class doc comment: {clip(class_doc, 800)}")
    tail = [
        "Calls (what each one does):", *(callee_lines or ["- (none)"]),
        "Reads fields:", *fmt(reads),
        "Writes fields:", *fmt(writes),
        *extra,
        "Called by (names only): " + (", ".join(caller_names) or "(nothing in this project)"),
    ]
    src = view.source(fn)
    fixed = len("\n".join(head + tail)) + 60
    src_budget = max(budget - fixed, 2000)
    src_text = trim_middle(src, src_budget) if src is not None else "(source code not available)"
    text = "\n".join([*head, "Source:", "```cpp", src_text, "```", *tail])
    parts = [fn.qname, fn.signature, src_text, callee_parts, [f.qname for f in reads], [f.qname for f in writes],
             fn.external_calls, fn.indirect_lines]
    return text, parts


def build_prompt(
    view: GraphView, group: list[Fn], summaries: dict[str, str], pending: set[str]
) -> tuple[str, list]:
    per = MAX_CONTEXT_CHARS // len(group)
    sections, parts = [], []
    for fn in group:
        t, p = build_function_section(view, fn, summaries, per, pending)
        sections.append(t)
        parts.append(p)
    if len(group) == 1:
        ask = f"Explain this function. Answer with one JSON object shaped like:\n{ANSWER_SHAPE}"
    else:
        ask = (
            "These functions call each other in a loop, so explain them together. Answer with one JSON object "
            '{"functions": [ ... ]} holding one entry per function, each shaped like the following plus an '
            f'"id" field copied from the heading:\n{ANSWER_SHAPE}'
        )
    return "\n\n".join(sections) + "\n\n" + ask, parts


# ---------------------------------------------------------------- 3. checking answers


@dataclass
class Answer:
    short: str = ""
    detailed: str = ""
    inputs: str = ""
    outputs: str = ""
    side_effects: list[str] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)


def _as_text(v) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else json.dumps(v))


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return [_as_text(x) for x in v if _as_text(x)]


def extract_json(text: str):
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        return json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b <= a:
            raise
        return json.loads(t[a : b + 1])


def parse_answers(text: str, ids: list[str]) -> tuple[dict[str, Answer], dict[str, str]]:
    """-> (answers by function id, problems by function id)"""
    answers: dict[str, Answer] = {}
    problems: dict[str, str] = {}
    try:
        data = extract_json(text)
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except ValueError as e:
        return {}, {i: f"the answer was not valid JSON ({e})" for i in ids}
    items = {None: data} if len(ids) == 1 and "functions" not in data else {}
    if not items:
        raw = data.get("functions")
        if isinstance(raw, list):
            for it in raw:
                if isinstance(it, dict) and it.get("id") in ids:
                    items[it["id"]] = it
        if len(ids) == 1 and not items and isinstance(data, dict) and "short" in data:
            items = {None: data}
    for i in ids:
        it = items.get(i) or (items.get(None) if len(ids) == 1 else None)
        if not it or not _as_text(it.get("short")):
            problems[i] = "no usable answer was given for this function"
            continue
        answers[i] = Answer(
            short=_as_text(it.get("short")), detailed=_as_text(it.get("detailed")),
            inputs=_as_text(it.get("inputs")), outputs=_as_text(it.get("outputs")),
            side_effects=_as_list(it.get("side_effects")), mentions=_as_list(it.get("mentions")),
        )
    return answers, problems


def _looks_like_identifier(tok: str) -> bool:
    return tok.startswith("_") or "_" in tok or any(c.isdigit() for c in tok) or (
        any(c.isupper() for c in tok[1:]) or (tok[:1].isupper() and len(tok) > 1)
    )


def verify_answer(view: GraphView, fn: Fn, ans: Answer) -> list[str]:
    """Reasons the answer cannot be trusted (empty list = verified)."""
    reasons: list[str] = []
    callees = set(view.callees.get(fn.id, {}))
    callers = set(view.callers.get(fn.id, ()))
    reads = set(view.reads.get(fn.id, {}))
    writes = set(view.writes.get(fn.id, {}))
    near = callees | callers | reads | writes | {fn.id}

    def candidates(m: str) -> set[str]:
        out = set()
        for i, f in view.fns.items():
            if f.qname == m or f.name == m or f.qname.endswith("::" + m):
                out.add(i)
        for i, f in view.fields.items():
            if f.qname == m or f.name == m or f.qname.endswith("::" + m):
                out.add(i)
        return out

    for raw in ans.mentions:
        m = re.sub(r"\(.*\)\s*$", "", raw.strip().strip("`'\"")).strip()
        if not m:
            continue
        cand = candidates(m)
        if not cand:
            reasons.append(f"mentions '{m}', which does not exist in the code graph")
        elif not (cand & near):
            reasons.append(f"mentions '{m}', which is not a callee, caller or field this function touches")

    fields_by_name: dict[str, set[str]] = {}
    for i, f in view.fields.items():
        fields_by_name.setdefault(f.name, set()).add(i)
    for effect in ans.side_effects:
        for tok in set(re.findall(r"[A-Za-z_]\w*", effect)):
            ids = fields_by_name.get(tok)
            if not ids or ids & writes:
                continue
            if ids & reads:
                reasons.append(f"side_effects names field '{tok}', but this function only reads it")
            elif _looks_like_identifier(tok):
                reasons.append(f"side_effects names field '{tok}', but this function does not write it")
    return reasons


# ---------------------------------------------------------------- run


@dataclass
class RunResult:
    groups_done: list[list[str]] = field(default_factory=list)  # completion order
    llm_calls: int = 0
    cache_hits: int = 0
    templates: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    unverified: int = 0
    deferred_by_max: int = 0
    rollups_written: int = 0
    rollups_skipped: int = 0
    stories_written: int = 0
    stories_skipped: int = 0
    skipped_offline: int = 0  # needed the API, but we are offline


@dataclass
class Estimate:
    functions: int
    cached: int
    calls: int
    input_tokens: int
    output_tokens: int
    dollars: float | None
    templates: int
    models: dict[str, int]


class Aborted(Exception):
    pass


class OfflineLLM:
    """Stands in for the API when AEROTRACE_OFFLINE=1. Anything that reaches it is a bug."""

    async def complete(self, **_kw) -> LLMResult:
        raise RuntimeError("AEROTRACE_OFFLINE is set: the API must not be called")


def offline_requested() -> bool:
    return os.environ.get("AEROTRACE_OFFLINE", "").strip().lower() in ("1", "true", "yes")


class Explainer:
    def __init__(
        self, conn: sqlite3.Connection, llm: LLM, *, model: str | None = None, fast_model: str | None = None,
        concurrency: int = 8, log: Callable[[str], None] = print, offline: bool = False,
    ):
        migrate(conn)
        self.offline = offline
        self.conn = conn
        self.llm = llm
        self.model = model or os.environ.get("AEROTRACE_MODEL") or DEFAULT_MODEL
        self.fast_model = fast_model or os.environ.get("AEROTRACE_FAST_MODEL") or DEFAULT_FAST_MODEL
        self.sem = asyncio.Semaphore(max(1, concurrency))
        self.log = log
        self.view = GraphView(conn)
        self.summaries: dict[str, str] = {
            r["function_id"]: r["short"] for r in conn.execute("SELECT function_id, short FROM summaries WHERE short IS NOT NULL")
        }
        self.stored_hash: dict[str, str] = {
            r["function_id"]: r["prompt_hash"] for r in conn.execute("SELECT function_id, prompt_hash FROM summaries")
        }
        self.result = RunResult()

    # -- targets and model choice
    def targets(self, only_reachable: bool) -> set[str]:
        keep = self.view.reachable_from_entry_points() if only_reachable else None
        return {i for i, f in self.view.fns.items() if not f.templated and (keep is None or i in keep)}

    def model_for(self, group: list[Fn]) -> str:
        return self.fast_model if all(f.loc < FAST_MODEL_MAX_LOC for f in group) else self.model

    def group_hash(self, group: list[Fn], model: str, parts: list) -> str:
        return sha(PROMPT_VERSION, model, [f.id for f in group], parts)

    def _prepare(self, ids: list[str]):
        group = [self.view.fns[i] for i in ids]
        prompt, parts = build_prompt(self.view, group, self.summaries, set(ids))  # set(ids): placeholders for same-cycle callees
        model = self.model_for(group)
        return group, prompt, self.group_hash(group, model, parts), model

    def is_cached(self, ids: list[str]) -> bool:
        _, _, h, _ = self._prepare(ids)
        return all(self.stored_hash.get(i) == h for i in ids)

    # -- cost estimate
    def estimate(self, plan: Plan, max_functions: int | None = None) -> Estimate:
        cached = calls = tin = tout = fns = 0
        models: dict[str, int] = {}
        cost = {}  # model -> [input tokens, output tokens]
        budget = max_functions
        for g in plan.groups:
            if self.is_cached(g):
                cached += len(g)
                continue
            if budget is not None and budget <= 0:
                break
            if budget is not None:
                g = g[:budget]
                budget -= len(g)
            for chunk in _chunks(g):
                group, prompt, _, model = self._prepare(chunk)
                calls += 1
                fns += len(chunk)
                t_in = (len(SYSTEM_PROMPT) + len(prompt)) // 4
                t_out = EXPECTED_OUTPUT_TOKENS * len(chunk)
                tin += t_in
                tout += t_out
                models[model] = models.get(model, 0) + 1
                c = cost.setdefault(model, [0, 0])
                c[0] += t_in
                c[1] += t_out
        dollars = None
        if models and all(m in PRICES for m in models):
            dollars = sum((i * PRICES[m][0] + o * PRICES[m][1]) / 1e6 for m, (i, o) in cost.items())
        templates = sum(1 for f in self.view.fns.values() if f.templated)
        return Estimate(fns, cached, calls, tin, tout, dollars, templates, models)

    # -- templates
    def write_templates(self) -> None:
        for f in self.view.fns.values():
            if not f.templated:
                continue
            t = template_summary(self.view, f)
            self._save(f.id, t["short"], t["detailed"], "", "", t["side_effects"], t["mentions"], "template",
                       sha("template", PROMPT_VERSION, t["short"]), True, [])
            self.summaries[f.id] = t["short"]
            self.result.templates += 1
        self.conn.commit()

    def _save(self, fid, short, detailed, inputs, outputs, side_effects, mentions, model, h, verified, reasons) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO summaries (function_id, short, detailed, model, prompt_hash, created_at,
                   inputs, outputs, side_effects_json, mentions_json, verified, reasons_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (fid, short, detailed, model, h, now(), inputs, outputs, json.dumps(side_effects), json.dumps(mentions),
             int(verified), json.dumps(reasons)),
        )
        self.stored_hash[fid] = h
        self.summaries[fid] = short

    # -- one call (with the concurrency limit)
    async def _ask(self, model: str, messages: list[dict], max_tokens: int) -> str:
        async with self.sem:
            res = await self.llm.complete(model=model, system=SYSTEM_PROMPT, messages=list(messages), max_tokens=max_tokens)
        self.result.llm_calls += 1
        self.result.input_tokens += res.input_tokens
        self.result.output_tokens += res.output_tokens
        return res.text

    # -- one group: a single function or one cycle (in chunks if it is huge)
    async def process_group(self, ids: list[str]) -> None:
        for chunk in _chunks(ids):
            await self._process_chunk(chunk)
        self.result.groups_done.append(list(ids))

    async def _process_chunk(self, ids: list[str]) -> None:
        group, prompt, h, model = self._prepare(ids)
        if all(self.stored_hash.get(i) == h for i in ids):
            self.result.cache_hits += len(ids)
            return
        if self.offline:  # keep whatever is saved; never reach for the API
            self.result.skipped_offline += len(ids)
            return
        max_tokens = 4000 + 1000 * len(ids)
        messages = [{"role": "user", "content": prompt}]
        text = await self._ask(model, messages, max_tokens)
        answers, problems = parse_answers(text, ids)
        reasons = {i: ([problems[i]] if i in problems else verify_answer(self.view, self.view.fns[i], answers[i])) for i in ids}
        bad = {i: r for i, r in reasons.items() if r}
        if bad:  # one retry, with the problems fed back
            feedback = "Your previous answer had problems:\n" + "\n".join(
                f"- {self.view.fns[i].qname}: {'; '.join(r)}" for i, r in bad.items()
            ) + (
                "\nFix them. Only name functions and fields from the lists you were given, exactly as shown, and "
                "only list a field as a side effect if it is in the Writes list. Return the full JSON again."
            )
            messages += [{"role": "assistant", "content": text}, {"role": "user", "content": feedback}]
            text2 = await self._ask(model, messages, max_tokens)
            answers2, problems2 = parse_answers(text2, ids)
            for i in bad:
                if i in answers2:  # a newer readable answer replaces the old one, even if it is still flagged
                    answers[i] = answers2[i]
                    reasons[i] = verify_answer(self.view, self.view.fns[i], answers2[i])
                elif i in answers:
                    reasons[i] = reasons[i] + ["the retry could not be read; kept the first answer"]
                else:
                    reasons[i] = [problems2.get(i, problems[i])]
        for i in ids:
            a = answers.get(i)
            r = reasons[i]
            if a is None:
                a = Answer(short="No summary: the model's answer could not be read.", detailed="")
            self._save(i, a.short, a.detailed, a.inputs, a.outputs, a.side_effects, a.mentions, model, h, not r, r)
            self.result.unverified += bool(r)
        self.conn.commit()

    # -- all functions, level by level
    async def run_functions(self, plan: Plan, max_functions: int | None) -> None:
        budget = max_functions
        for n, level in enumerate(plan.levels, start=1):
            todo = []
            for g in level:
                if self.is_cached(g):
                    todo.append(g)  # free; processed to keep bookkeeping and callee summaries identical
                    continue
                if budget is not None:
                    if budget <= 0:
                        self.result.deferred_by_max += len(g)
                        continue
                    if len(g) > budget:
                        self.result.deferred_by_max += len(g) - budget
                        g = g[:budget]
                    budget -= len(g)
                todo.append(g)
            if not todo:
                continue
            self.log(f"level {n}/{len(plan.levels)}: {sum(len(g) for g in todo)} functions")
            await asyncio.gather(*(self.process_group(g) for g in todo))

    # -- 7. roll-ups
    async def run_rollups(self) -> None:
        v = self.view
        classes: dict[str, list[Fn]] = {}
        files: dict[str, list[Fn]] = {}
        for f in v.fns.values():
            files.setdefault(f.file, []).append(f)
            if f.cls:
                classes.setdefault(f.cls, []).append(f)
        jobs = [("class", k, m) for k, m in sorted(classes.items())] + [("file", k, m) for k, m in sorted(files.items())]
        await asyncio.gather(*(self._rollup(kind, key, members) for kind, key, members in jobs))
        self.conn.commit()

    async def _rollup(self, kind: str, key: str, members: list[Fn]) -> None:
        v = self.view
        needed = [m for m in members if not m.templated]
        if any(m.id not in self.summaries for m in needed):
            self.result.rollups_skipped += 1  # incomplete: never summarize from partial information
            return
        ranked = sorted(members, key=lambda m: (m.is_noise, -m.reach, m.qname))
        shown = [f"- {m.name}: {clip(self.summaries[m.id], 160)}" for m in ranked[:40] if not m.is_noise]
        helpers = [m.name for m in ranked if m.is_noise]
        lines = [f"{kind.title()}: {key}"]
        if kind == "class":
            if v.class_docs.get(key):
                lines.append(f"Doc comment: {clip(v.class_docs[key], 800)}")
            if v.class_bases.get(key):
                lines.append("Built on: " + ", ".join(v.class_bases[key]))
            fs = [f for f in v.fields.values() if f.container and f.qname.rsplit("::", 1)[0] == key]
            if fs:
                lines.append("Fields: " + ", ".join(f"{f.name} ({f.type})" for f in sorted(fs, key=lambda f: f.name)[:40]))
        else:
            cls = sorted({m.cls for m in members if m.cls})
            if cls:
                lines.append("Classes with code here: " + ", ".join(cls[:20]))
        lines += ["Functions (what each does):", *(shown or ["- (none)"])]
        if helpers:
            lines.append(f"Plus {len(helpers)} small helper functions: " + ", ".join(helpers[:15]))
        prompt = (
            "\n".join(lines)
            + f"\n\nWrite a plain-English summary of what this {kind} is for and why it matters to the program, using "
            'only the information above. Say "unclear from the code" where it is not clear. Answer with one JSON object: '
            '{"short": "one sentence, at most 25 words", "detailed": "3 to 5 short sentences for a new engineer"}'
        )
        h = sha(PROMPT_VERSION, self.model, kind, key, prompt)
        row = self.conn.execute("SELECT prompt_hash FROM rollups WHERE kind=? AND key=?", (kind, key)).fetchone()
        if row and row["prompt_hash"] == h:
            return
        if self.offline:
            self.result.skipped_offline += 1
            return
        try:
            data = extract_json(await self._ask(self.model, [{"role": "user", "content": prompt}], 4000))
            short, detailed = _as_text(data.get("short")), _as_text(data.get("detailed"))
        except (ValueError, AttributeError):
            short = detailed = ""
        if not short:
            self.result.rollups_skipped += 1
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO rollups (kind, key, short, detailed, model, prompt_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (kind, key, short, detailed, self.model, h, now()),
        )
        self.result.rollups_written += 1

    # -- 5. flow stories
    def story_tree(self, root: str) -> tuple[list[str], bool]:
        """Outline of what the entry point calls (depth 3, non-noise, in call order) and whether every
        function in it has a summary."""
        v = self.view
        lines: list[str] = []
        complete = True
        count = [0]

        def walk(fid: str, depth: int, ancestors: tuple[str, ...]) -> None:
            nonlocal complete
            f = v.fns[fid]
            s = self.summaries.get(fid)
            complete = complete and bool(s)
            count[0] += 1
            lines.append(f"{'  ' * depth}- {f.qname}: {clip(s, 200) if s else '(no summary)'}")
            if depth >= STORY_DEPTH:
                return
            kids = [c for c in v.callees.get(fid, {}) if not v.fns[c].is_noise]
            kids.sort(key=lambda c: (min(v.callees[fid][c]["lines"] or [0]), v.fns[c].qname))
            for c in kids[:STORY_MAX_CHILDREN]:
                if c in ancestors or c == fid or count[0] >= STORY_MAX_NODES:
                    continue
                walk(c, depth + 1, ancestors + (fid,))

        walk(root, 0, ())
        return lines, complete

    async def run_stories(self, max_stories: int | None) -> None:
        eps = [i for i in self.view.entry_points if i in self.view.fns]
        if max_stories is not None:
            eps = eps[:max_stories]

        async def one(eid: str) -> None:
            outline, complete = self.story_tree(eid)
            f = self.view.fns[eid]
            if not complete:
                self.result.stories_skipped += 1
                return
            prompt = (
                f"Entry point: {f.qname}\nWhat it calls, in the order the calls appear in the code (indented = deeper):\n"
                + "\n".join(outline)
                + f"\n\nWrite a numbered story of what happens when {f.qname} runs, for a new engineer. Follow the order "
                "above, say why each step matters, use plain everyday English, and do not guess beyond what is written "
                'above (say "unclear from the code" if needed). Answer with one JSON object: '
                '{"steps": ["first thing that happens", "..."]} with 4 to 12 steps.'
            )
            h = sha(PROMPT_VERSION, self.model, "story", eid, prompt)
            row = self.conn.execute("SELECT prompt_hash FROM flow_stories WHERE function_id = ?", (eid,)).fetchone()
            if row and row["prompt_hash"] == h:
                return
            if self.offline:
                self.result.skipped_offline += 1
                return
            try:
                data = extract_json(await self._ask(self.model, [{"role": "user", "content": prompt}], 6000))
                steps = _as_list(data.get("steps"))
            except (ValueError, AttributeError):
                steps = []
            if not steps:
                self.result.stories_skipped += 1
                return
            steps = [re.sub(r"^\d+[.)]\s*", "", s) for s in steps]
            story = "\n".join(f"{n}. {s}" for n, s in enumerate(steps, start=1))
            self.conn.execute(
                "INSERT OR REPLACE INTO flow_stories (function_id, story, steps_json, model, prompt_hash, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (eid, story, json.dumps(steps), self.model, h, now()),
            )
            self.result.stories_written += 1

        await asyncio.gather(*(one(e) for e in eps))
        self.conn.commit()

    # -- everything
    async def run(
        self, *, only_reachable: bool = False, max_functions: int | None = None, rollups: bool = True,
        stories: bool = True, max_stories: int | None = None,
    ) -> RunResult:
        plan = make_plan(self.view, self.targets(only_reachable))
        self.write_templates()
        await self.run_functions(plan, max_functions)
        rebuild_fts(self.conn)
        if rollups:
            await self.run_rollups()
        if stories:
            await self.run_stories(max_stories)
        self.conn.commit()
        rebuild_fts(self.conn)
        self.conn.commit()
        return self.result


def _chunks(ids: list[str]) -> list[list[str]]:
    return [ids[i : i + MAX_CYCLE_CHUNK] for i in range(0, len(ids), MAX_CYCLE_CHUNK)]


# ---------------------------------------------------------------- CLI


def format_estimate(e: Estimate, models: tuple[str, str]) -> str:
    cost = f" (about ${e.dollars:.2f})" if e.dollars is not None else ""
    return (
        f"Plan: {e.functions} functions to summarize in {e.calls} API calls "
        f"({e.cached} already done and unchanged, {e.templates} get a template sentence with no API call).\n"
        f"Rough cost estimate: ~{e.input_tokens:,} input + ~{e.output_tokens:,} output tokens{cost}, "
        f"not counting roll-ups and stories. Models: {', '.join(f'{m} x{n}' for m, n in e.models.items()) or 'none'}."
    )


def main(argv: list[str] | None = None, llm: LLM | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m extractor.common.explain", description=__doc__.split("\n")[0])
    ap.add_argument("repo", help="repo name (reads data/<repo>.db)")
    ap.add_argument("--only-reachable-from-entry-points", action="store_true")
    ap.add_argument("--max", type=int, default=None, metavar="N", help="summarize at most N functions that need the API")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--skip-rollups", action="store_true")
    ap.add_argument("--skip-stories", action="store_true")
    ap.add_argument("--max-stories", type=int, default=None)
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--offline", action="store_true", help="never call the API: keep saved explanations, write template sentences (same as AEROTRACE_OFFLINE=1)")
    args = ap.parse_args(argv)
    offline = args.offline or offline_requested()

    data_dir = Path(args.data_dir or os.environ.get("AEROTRACE_DATA") or DATA_DIR)
    db = data_dir / f"{Path(args.repo).name}.db"
    if not db.is_file():
        print(f"error: {db} not found. Load the graph first: python -m server.store load data/{db.stem}.graph.json", file=sys.stderr)
        return 2
    conn = connect(db)
    try:
        if offline:
            llm = OfflineLLM()
        if llm is None:
            try:
                llm = AnthropicLLM()
            except Exception as e:  # missing credentials etc.
                print(f"error: could not create the Anthropic client ({e})", file=sys.stderr)
                return 2
        ex = Explainer(conn, llm, concurrency=args.concurrency, offline=offline)
        plan = make_plan(ex.view, ex.targets(args.only_reachable_from_entry_points))
        est = ex.estimate(plan, args.max)
        if offline:
            print(f"Offline: {est.cached} functions already explained are kept; {est.functions} have no saved explanation and will not be written (no API calls).")
        else:
            print(format_estimate(est, (ex.model, ex.fast_model)))
        if est.calls and not args.yes and not offline:
            if not sys.stdin.isatty():
                print("Not confirmed: pass --yes to run without asking.", file=sys.stderr)
                return 2
            if input("Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Cancelled; nothing was sent.")
                return 1
        try:
            res = asyncio.run(
                ex.run(
                    only_reachable=args.only_reachable_from_entry_points, max_functions=args.max,
                    rollups=not args.skip_rollups, stories=not args.skip_stories, max_stories=args.max_stories,
                )
            )
        except Exception as e:
            name = type(e).__name__
            if "Authentication" in name or "PermissionDenied" in name:
                print(f"error: the API rejected the credentials ({e}). Set ANTHROPIC_API_KEY or run `ant auth login`.", file=sys.stderr)
                return 2
            raise
        print(
            f"done: {res.llm_calls} API calls, {res.input_tokens:,} input / {res.output_tokens:,} output tokens; "
            f"{res.cache_hits} functions reused from cache, {res.templates} template summaries, "
            f"{res.unverified} flagged verified=false, {res.deferred_by_max} left for later (--max), "
            f"{res.skipped_offline} skipped because offline; "
            f"roll-ups {res.rollups_written} written / {res.rollups_skipped} skipped; "
            f"stories {res.stories_written} written / {res.stories_skipped} skipped."
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
