"""The explain step, with a fake LLM: no network, no API key."""

import asyncio
import json
import re
import shutil
import textwrap
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from extractor.common import explain
from extractor.common.config import RepoConfig
from extractor.common.enrich import enrich_graph
from extractor.common.explain import AnthropicLLM, Explainer, GraphView, LLMResult, make_plan
from extractor.cpp.build import extract_repo
from server import store
from server.main import create_app

CODE = """
struct Box { int v; int get() const { return v; } void set(int x) { v = x; } };
int leaf1() { return 1; }
int leaf2() { return 2; }
int mid(int x) { return leaf1() + x; }
int top() { return mid(1) + leaf2(); }
int pong(int n);
int ping(int n) { return n > 0 ? pong(n - 1) : 0; }
int pong(int n) { return n > 0 ? ping(n - 1) : 0; }
void writer(Box& b) { b.set(1); b.v = 5; }
int reader(Box& b) { return b.v; }
int peek(Box& b) { int x = b.v; x += leaf1(); return x; }
int big(int n) {
  int total = 0;
  for (int i = 0; i < n; i++) {
    total += i;
    total += i * 2;
    total += i * 3;
    total += i * 4;
    total += i * 5;
    total += i * 6;
    total += i * 7;
    total += i * 8;
    total += i * 9;
  }
  return total;
}
"""


def qn(name):
    return name.rsplit("::", 1)[-1]


class FakeLLM:
    """Answers from the prompt. `script[qualified_name]` = fn(attempt) -> dict of answer fields."""

    def __init__(self, script=None, suffix=""):
        self.calls = []
        self.script = script or {}
        self.suffix = suffix

    async def complete(self, *, model, system, messages, max_tokens):
        prompt = messages[0]["content"]
        self.calls.append({"model": model, "system": system, "messages": messages, "prompt": prompt})
        if prompt.startswith("Entry point:"):
            return LLMResult(json.dumps({"steps": ["It starts.", "2) Then it finishes."]}), 10, 5)
        if prompt.startswith(("Class:", "File:")):
            return LLMResult(json.dumps({"short": "Rollup short.", "detailed": "Rollup detailed."}), 10, 5)
        attempt = (len(messages) + 1) // 2
        found = re.findall(r"### Function: (\S+)   \(id: (\w+)\)", prompt)
        items = []
        for q, fid in found:
            ans = {
                "short": f"Does {qn(q)}{self.suffix}.", "detailed": "Detail.", "inputs": "x", "outputs": "y",
                "side_effects": [], "mentions": [],
            }
            if q in self.script:
                ans.update(self.script[q](attempt))
            items.append({"id": fid, **ans})
        body = items[0] if len(items) == 1 else {"functions": items}
        if len(items) == 1:
            body = {k: v for k, v in items[0].items() if k != "id"}
        return LLMResult(json.dumps(body), 100, 50)

    def asked_about(self, name):
        return [c for c in self.calls if re.search(rf"### Function: (\S+::)?{name}   ", c["prompt"])]


@pytest.fixture(scope="module")
def synth(tmp_path_factory):
    repo = tmp_path_factory.mktemp("explain_repo")
    (repo / "x.cpp").write_text(textwrap.dedent(CODE))
    graph, _ = extract_repo(repo, cache_dir=None, jobs=1, log=lambda *_: None)
    enrich_graph(graph, RepoConfig(entry_points=["top"]))
    return repo, graph


@pytest.fixture
def db(synth, tmp_path):
    repo, graph = synth
    path = tmp_path / f"{graph.repo_name}.db"
    store.load_into_db(graph, path, repo)
    conn = store.connect(path)
    yield conn, path, repo
    conn.close()


def run(conn, llm, **kw):
    run_kw = {k: kw.pop(k) for k in ("only_reachable", "max_functions", "rollups", "stories", "max_stories") if k in kw}
    ex = Explainer(conn, llm, log=lambda *_: None, **kw)
    res = asyncio.run(ex.run(**run_kw))
    return ex, res


def summary(conn, ex, name):
    (fid,) = [i for i, f in ex.view.fns.items() if f.qname == name]
    return conn.execute("SELECT * FROM summaries WHERE function_id = ?", (fid,)).fetchone()


# ---------------------------------------------------------------- order

def test_callees_are_processed_before_callers(db):
    conn, _, _ = db
    llm = FakeLLM()
    ex, res = run(conn, llm)
    pos = {i: n for n, g in enumerate(res.groups_done) for i in g}
    v = ex.view
    targets = [i for i, f in v.fns.items() if not f.templated]
    assert set(targets) == set(pos)
    for f in targets:
        for c in v.callees.get(f, {}):
            if c in pos and c not in next(g for g in res.groups_done if f in g):
                assert pos[c] < pos[f], f"{v.fns[c].qname} must come before {v.fns[f].qname}"


def test_plan_levels_and_cycle_group(db):
    conn, _, _ = db
    v = GraphView(conn)
    plan = make_plan(v, {i for i, f in v.fns.items() if not f.templated})
    names = [[[v.fns[i].name for i in g] for g in lvl] for lvl in plan.levels]
    assert ["leaf1"] in names[0] and ["leaf2"] in names[0]
    assert ["ping", "pong"] in names[0]  # a cycle is one group
    level_of = {n: li for li, lvl in enumerate(names) for g in lvl for n in g}
    assert level_of["leaf1"] < level_of["mid"] < level_of["top"]
    assert level_of["leaf2"] < level_of["top"]


def test_caller_prompt_contains_callee_summaries(db):
    conn, _, _ = db
    llm = FakeLLM()
    run(conn, llm)
    (top,) = llm.asked_about("top")
    assert "mid: Does mid." in top["prompt"] and "leaf2: Does leaf2." in top["prompt"]
    (mid,) = llm.asked_about("mid")
    assert "leaf1: Does leaf1." in mid["prompt"]
    assert "Called by (names only): top" in mid["prompt"]


def test_a_cycle_is_one_call(db):
    conn, _, _ = db
    llm = FakeLLM()
    run(conn, llm)
    both = [c for c in llm.calls if "Function: ping" in c["prompt"] and "Function: pong" in c["prompt"]]
    assert len(both) == 1 and len(llm.asked_about("ping")) == 1


def test_noise_functions_get_template_sentences_without_the_api(db):
    conn, _, _ = db
    llm = FakeLLM()
    ex, res = run(conn, llm)
    r = summary(conn, ex, "reader")
    assert r["short"] == "Returns the value of v." and r["model"] == "template" and r["verified"] == 1
    assert llm.asked_about("reader") == [] and llm.asked_about("get") == [] and llm.asked_about("set") == []
    assert summary(conn, ex, "Box::set")["short"] == "Sets v to the value it is given."
    assert res.templates >= 3


def test_short_functions_use_the_fast_model_and_long_ones_the_main_model(db):
    conn, _, _ = db
    llm = FakeLLM()
    ex, _ = run(conn, llm, model="main-model", fast_model="fast-model")
    assert llm.asked_about("leaf1")[0]["model"] == "fast-model"
    assert llm.asked_about("big")[0]["model"] == "main-model"


# ---------------------------------------------------------------- checking answers

def test_mention_of_a_function_that_does_not_exist_is_unverified(db):
    conn, _, _ = db
    llm = FakeLLM({"mid": lambda attempt: {"mentions": ["ghost_fn"]}})
    ex, res = run(conn, llm)
    row = summary(conn, ex, "mid")
    reasons = json.loads(row["reasons_json"])
    assert row["verified"] == 0 and any("ghost_fn" in r and "does not exist" in r for r in reasons)
    assert len(llm.asked_about("mid")) == 2  # asked once more with the errors fed back
    assert row["short"] == "Does mid."  # kept, but flagged
    assert res.unverified >= 1


def test_mention_that_exists_but_is_not_one_hop_away_is_unverified(db):
    conn, _, _ = db
    llm = FakeLLM({"top": lambda attempt: {"mentions": ["mid", "leaf1"]}})  # leaf1 is two hops from top
    ex, _ = run(conn, llm)
    row = summary(conn, ex, "top")
    reasons = json.loads(row["reasons_json"])
    assert row["verified"] == 0
    assert any("leaf1" in r and "not a callee, caller or field" in r for r in reasons)
    assert not any("'mid'" in r for r in reasons)  # mid is a real callee


def test_side_effect_naming_a_field_that_is_only_read_is_unverified(db):
    conn, _, _ = db
    llm = FakeLLM({
        "peek": lambda attempt: {"side_effects": ["Changes v"]},
        "writer": lambda attempt: {"side_effects": ["Sets v"], "mentions": ["v"]},
    })
    ex, _ = run(conn, llm)
    peek = summary(conn, ex, "peek")
    assert peek["verified"] == 0 and any("only reads it" in r for r in json.loads(peek["reasons_json"]))
    assert summary(conn, ex, "writer")["verified"] == 1  # writer really writes v


def test_a_retry_with_the_errors_fed_back_can_fix_the_answer(db):
    conn, _, _ = db
    llm = FakeLLM({"mid": lambda attempt: {"mentions": ["ghost_fn"] if attempt == 1 else ["leaf1"]}})
    ex, _ = run(conn, llm)
    row = summary(conn, ex, "mid")
    assert row["verified"] == 1 and json.loads(row["mentions_json"]) == ["leaf1"]
    first, second = llm.asked_about("mid")
    assert len(first["messages"]) == 1 and len(second["messages"]) == 3
    assert second["messages"][1]["role"] == "assistant"
    assert "ghost_fn" in second["messages"][2]["content"]


# ---------------------------------------------------------------- impact

def test_impact_facts_come_from_the_graph(db):
    conn, _, _ = db
    view = GraphView(conn)
    by_name = {f.qname: i for i, f in view.fns.items()}
    leaf1 = view.impact(by_name["leaf1"])
    # leaf1 <- mid <- top, and leaf1 <- peek
    assert leaf1.direct_callers == 2 and leaf1.upstream == 3
    assert {view.fns[a].qname for a in leaf1.affected} == {"mid", "peek", "top"}
    assert [view.fns[e].qname for e in leaf1.entry_points] == ["top"]
    assert leaf1.files == []  # everything is in x.cpp
    writer = view.impact(by_name["writer"])
    users = {view.fields[fl].qname: [view.fns[u].qname for u in us] for fl, us in writer.data_users.items()}
    assert users == {"Box::v": ["Box::get", "peek", "reader"]}  # other functions that read what writer changes
    assert writer.upstream == 0 and writer.direct == 3 and writer.level == "low"
    assert writer.reason.startswith("3 functions use it directly")
    assert leaf1.direct == 2 and "could reach 3 functions in all" in leaf1.reason  # top, through mid
    assert view.impact(by_name["big"]).reason == "Nothing else in this project calls it or reads the data it changes."
    top = view.impact(by_name["top"])
    assert top.is_entry_point and top.entry_points == [] and "the program starts here" in top.reason


def test_impact_level_counts_direct_users_and_their_files():
    lvl = lambda direct, files: explain.impact_level(direct, files, direct, False)[0]
    assert lvl(0, 0) == "low" and lvl(3, 1) == "low"
    assert lvl(4, 0) == "medium" and lvl(1, 2) == "medium"
    assert lvl(12, 0) == "high" and lvl(1, 5) == "high"
    assert explain.impact_level(1, 0, 500, False)[0] == "low"  # many callers of callers alone do not raise it


def test_the_prompt_lists_the_impact_facts(db):
    conn, _, _ = db
    llm = FakeLLM()
    run(conn, llm)
    (call,) = llm.asked_about("writer")
    assert "Impact (worked out from the code graph):" in call["prompt"]
    assert "Box::v: read by Box::get, peek, reader" in call["prompt"]
    assert "Impact level: low." in call["prompt"]
    assert '"change_risks"' in call["prompt"] and "change_risks" in call["system"]


def test_role_and_change_risks_are_checked_saved_and_served(db):
    conn, path, _ = db
    llm = FakeLLM({
        # reader is two hops away in the call graph, but it reads the data writer changes: allowed
        "writer": lambda attempt: {
            "role": "Fills in a Box for later readers.",
            "change_risks": ["reader returns v, so a different value here changes what reader gives back."],
            "mentions": ["reader", "v"],
        },
    })
    ex, _ = run(conn, llm)
    row = summary(conn, ex, "writer")
    assert row["verified"] == 1 and len(llm.asked_about("writer")) == 1
    assert row["role"] == "Fills in a Box for later readers." and row["impact_level"] == "low"
    api = TestClient(create_app(path.parent))
    (wid,) = [i for i, f in ex.view.fns.items() if f.qname == "writer"]
    s = api.get(f"/api/repos/{path.stem}/functions/{wid}").json()["summary"]
    assert s["change_risks"] == ["reader returns v, so a different value here changes what reader gives back."]
    assert s["impact"]["data_users"] == [{"field": "Box::v", "readers": ["Box::get", "peek", "reader"], "readers_total": 3}]
    assert s["impact"]["affected"] == 3 and "impact_json" not in s and "change_risks_json" not in s


def test_impact_tab_shows_severity_how_effort_and_business_impact(db):
    conn, path, _ = db
    llm = FakeLLM({"writer": lambda attempt: {"business_impact": "Boxes would hold the wrong value for every reader."}})
    ex, _ = run(conn, llm)
    api = TestClient(create_app(path.parent))
    fid = {f.qname: i for i, f in ex.view.fns.items()}

    d = api.get(f"/api/repos/{path.stem}/impact/{fid['writer']}").json()
    assert d["total"] == 0  # nothing calls writer...
    rows = {a["qualified_name"]: a for a in d["affected"]}
    assert set(rows) == {"Box::get", "peek", "reader"}  # ...but three functions read the v it changes
    assert rows["reader"]["how"] == ["Reads v, which this function changes"]
    assert rows["reader"]["distance"] == 1 and rows["reader"]["severity"] == "high" and rows["reader"]["effort_hours"] == 1.0
    assert d["severity"] == "low" and d["severity_reason"].startswith("3 functions use it directly")
    assert d["business_impact"] == "Boxes would hold the wrong value for every reader." and d["business_impact_checked"] is True
    # change 1 h + three direct readers at 1 h each, nothing further away: 4 h, shown as 3-6 h
    assert d["effort"] == {"change": 1.0, "checks": 3.0, "low": 3.0, "high": 6.0}

    d = api.get(f"/api/repos/{path.stem}/impact/{fid['leaf1']}").json()
    rows = {a["qualified_name"]: a for a in d["affected"]}
    assert rows["mid"]["how"] == ["Calls it directly"] and rows["mid"]["severity"] == "high"
    assert rows["top"]["how"] == ["Calls it through mid"] and rows["top"]["severity"] == "medium"
    assert rows["top"]["effort_hours"] == 0.0  # covered by the one full test run
    assert d["effort"]["checks"] == 2.0 + explain.RETEST_HOURS  # mid and peek, plus the full run for top
    assert d["business_impact"] is None and d["business_impact_checked"] is None  # FakeLLM wrote none for leaf1
    same = ex.view.impact(fid["leaf1"])  # the Explain tab's numbers
    assert (d["severity"], d["severity_reason"]) == (same.level, same.reason)


def test_severity_and_effort_rules():
    assert explain.affected_severity(1, False)[0] == "high"
    assert explain.affected_severity(2, False)[0] == "medium"
    assert explain.affected_severity(3, False)[0] == "low"
    assert explain.affected_severity(4, True)[0] == "medium"  # where the program starts is never low
    assert explain.effort_hours("high", 10) == 1.0 and explain.effort_hours("high", 50) == 1.5
    assert explain.effort_hours("high", 500) == 2.0 and explain.effort_hours("low", 500) == 0.0
    assert explain.effort_range(10, 0.0, False) == {"change": 1.0, "checks": 0.0, "low": 1.0, "high": 1.5}


def test_impact_numbers_are_refreshed_on_every_run_without_api_calls(db):
    conn, _, _ = db
    ex, _ = run(conn, FakeLLM())
    conn.execute("UPDATE summaries SET impact_level = 'high', impact_json = NULL")
    conn.commit()
    llm = FakeLLM()
    ex, res = run(conn, llm)
    assert res.llm_calls == 0 and not llm.calls
    row = summary(conn, ex, "leaf1")
    assert row["impact_level"] == "low" and json.loads(row["impact_json"])["callers_any_distance"] == 3
    assert summary(conn, ex, "Box::get")["impact_json"]  # template summaries get impact facts too


def test_unreadable_answers_are_flagged_not_dropped(db):
    conn, _, _ = db

    class Garbage(FakeLLM):
        async def complete(self, **kw):
            if kw["messages"][0]["content"].startswith(("Class:", "File:", "Entry point:")):
                return await super().complete(**kw)
            self.calls.append(kw)
            return LLMResult("Sorry, I cannot do that.", 1, 1)

    ex, _ = run(conn, Garbage(), stories=False, rollups=False)
    row = summary(conn, ex, "leaf1")
    assert row["verified"] == 0 and "could not be read" in row["short"]


def test_json_in_fences_is_accepted():
    answers, problems = explain.parse_answers('```json\n{"short": "Hi.", "mentions": "x"}\n```', ["a"])
    assert answers["a"].short == "Hi." and answers["a"].mentions == ["x"] and not problems


# ---------------------------------------------------------------- cache and limits

def test_second_run_makes_zero_api_calls(db):
    conn, _, _ = db
    first = FakeLLM()
    run(conn, first)
    assert first.calls
    second = FakeLLM()
    ex, res = run(conn, second)
    assert second.calls == [] and res.llm_calls == 0
    assert res.cache_hits >= 7


def test_changed_code_only_reprocesses_what_it_affects(db):
    conn, _, repo = db
    run(conn, FakeLLM())
    src = (repo / "x.cpp").read_text()
    try:
        (repo / "x.cpp").write_text(src.replace("int leaf1() { return 1; }", "int leaf1() { return 11; }"))
        again = FakeLLM()  # same wording as before: the callers' inputs did not change
        run(conn, again, rollups=False, stories=False)
        assert [len(again.asked_about(n)) for n in ("leaf1", "mid", "top")] == [1, 0, 0]
        changed = FakeLLM(suffix=" now")  # new wording ripples up to everything that calls it
        (repo / "x.cpp").write_text(src.replace("int leaf1() { return 1; }", "int leaf1() { return 12; }"))
        run(conn, changed, rollups=False, stories=False)
        assert [len(changed.asked_about(n)) for n in ("leaf1", "mid", "top", "peek", "leaf2")] == [1, 1, 1, 1, 0]
    finally:
        (repo / "x.cpp").write_text(src)


def test_max_limits_new_summaries_and_keeps_callee_order(db):
    conn, _, _ = db
    llm = FakeLLM()
    ex, res = run(conn, llm, max_functions=2)
    done = [i for g in res.groups_done for i in g]
    assert len(done) == 2 and res.deferred_by_max >= 5
    assert all(not ex.view.callees.get(i) or all(ex.view.fns[c].templated for c in ex.view.callees[i]) for i in done)
    assert (res.rollups_written, res.stories_written) == (0, 0) or res.rollups_skipped > 0
    assert res.stories_skipped >= 1  # the story for `top` needs summaries that do not exist yet


def test_only_reachable_from_entry_points(db):
    conn, _, _ = db
    llm = FakeLLM()
    ex, res = run(conn, llm, only_reachable=True, rollups=False)
    asked = {n for n in ("top", "mid", "leaf1", "leaf2", "ping", "writer", "peek", "big") if llm.asked_about(n)}
    assert asked == {"top", "mid", "leaf1", "leaf2"}


# ---------------------------------------------------------------- stories, roll-ups, API

def test_stories_and_rollups_are_stored_and_served(db):
    conn, path, _ = db
    ex, res = run(conn, FakeLLM())
    assert res.stories_written == 1 and res.rollups_written >= 2
    api = TestClient(create_app(path.parent))
    repo = path.stem
    (top,) = [i for i, f in ex.view.fns.items() if f.qname == "top"]
    story = api.get(f"/api/repos/{repo}/story/{top}").json()
    assert story["story"] == "1. It starts.\n2. Then it finishes." and story["function"]["name"] == "top"
    (mid,) = [i for i, f in ex.view.fns.items() if f.qname == "mid"]
    assert "No story" in api.get(f"/api/repos/{repo}/story/{mid}").json()["detail"]
    assert api.get(f"/api/repos/{repo}/story/{mid}").status_code == 404
    assert [c["key"] for c in api.get(f"/api/repos/{repo}/classes").json()] == ["Box"]
    box = api.get(f"/api/repos/{repo}/classes/Box").json()
    assert box["short"] == "Rollup short." and {f["name"] for f in box["functions"]} == {"get", "set"}
    assert api.get(f"/api/repos/{repo}/files/x.cpp").json()["detailed"] == "Rollup detailed."
    assert api.get(f"/api/repos/{repo}/classes/Nope").status_code == 404
    d = api.get(f"/api/repos/{repo}/functions/{mid}").json()["summary"]
    assert d["short"] == "Does mid." and d["verified"] is True and d["reasons"] == [] and d["model"]
    assert "prompt_hash" not in d
    found = api.get(f"/api/repos/{repo}/search", params={"q": "does mid"}).json()["functions"]
    assert [f["name"] for f in found] == ["mid"]  # summaries are searchable after a run


def test_summaries_survive_reloading_the_graph(db, synth):
    conn, path, repo = db
    run(conn, FakeLLM())
    conn.close()
    store.load_into_db(synth[1], path, repo)
    c2 = store.connect(path)
    assert c2.execute("SELECT COUNT(*) FROM summaries").fetchone()[0] >= 10
    assert c2.execute("SELECT COUNT(*) FROM flow_stories").fetchone()[0] == 1
    assert c2.execute("SELECT COUNT(*) FROM rollups").fetchone()[0] >= 2
    c2.close()


def test_old_database_is_migrated(tmp_path):
    import sqlite3

    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE summaries (function_id TEXT PRIMARY KEY, short TEXT, detailed TEXT, model TEXT, prompt_hash TEXT, created_at TEXT)")
    store.migrate(c)
    store.migrate(c)  # twice is fine
    cols = {r[1] for r in c.execute("PRAGMA table_info(summaries)")}
    assert {"verified", "reasons_json", "side_effects_json", "role", "change_risks_json", "business_impact",
            "impact_level", "impact_json"} <= cols
    assert c.execute("SELECT COUNT(*) FROM flow_stories").fetchone()[0] == 0


# ---------------------------------------------------------------- context size, CLI, SDK call shape

def test_big_functions_are_trimmed_in_the_middle(tmp_path):
    body = "\n".join(f"  total += helper_{i % 7}({i});" for i in range(3000))
    (tmp_path / "x.cpp").write_text(
        "".join(f"int helper_{i}(int a) {{ return a; }}\n" for i in range(7)) + f"int huge(int n) {{\n  int total = 0;\n{body}\n  return total;\n}}\n"
    )
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    enrich_graph(graph)
    store.load_into_db(graph, tmp_path / "t.db", tmp_path)
    conn = store.connect(tmp_path / "t.db")
    view = GraphView(conn)
    (huge,) = [f for f in view.fns.values() if f.name == "huge"]
    prompt, _ = explain.build_prompt(view, [huge], {}, set())
    assert "lines trimmed from the middle" in prompt
    assert len(prompt) < explain.MAX_CONTEXT_CHARS + 1500  # about 6k tokens
    assert "int huge(int n)" in prompt and "return total;" in prompt  # both ends survive
    conn.close()


def test_cli_asks_before_spending_and_prints_an_estimate(db, capsys):
    conn, path, _ = db
    conn.close()
    llm = FakeLLM()
    assert explain.main([path.stem, "--data-dir", str(path.parent)], llm=llm) == 2  # no tty, no --yes
    out = capsys.readouterr()
    assert "Rough cost estimate" in out.out and "Not confirmed" in out.err and llm.calls == []
    assert explain.main([path.stem, "--data-dir", str(path.parent), "--yes", "--max", "3"], llm=llm) == 0
    assert "done:" in capsys.readouterr().out and llm.calls


def test_cli_reports_a_missing_database(tmp_path, capsys):
    assert explain.main(["nope", "--data-dir", str(tmp_path), "--yes"], llm=FakeLLM()) == 2
    assert "Load the graph first" in capsys.readouterr().err


def test_anthropic_request_shape(monkeypatch):
    seen = []

    class FakeMessages:
        async def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="{}"), SimpleNamespace(type="thinking", thinking="")],
                usage=SimpleNamespace(input_tokens=11, output_tokens=7),
                stop_reason="end_turn",
            )

    llm = AnthropicLLM(client=SimpleNamespace(messages=FakeMessages()))
    monkeypatch.delenv("AEROTRACE_EFFORT", raising=False)
    msgs = [{"role": "user", "content": "hi"}]
    res = asyncio.run(llm.complete(model="claude-sonnet-5-5", system="sys", messages=msgs, max_tokens=500))
    asyncio.run(llm.complete(model="claude-haiku-4-5-20251001", system="sys", messages=msgs, max_tokens=500))
    assert (res.text, res.input_tokens, res.output_tokens) == ("{}", 11, 7)
    sonnet, haiku = seen
    assert sonnet["model"] == "claude-sonnet-5-5" and sonnet["system"] == "sys" and sonnet["max_tokens"] == 500
    assert sonnet["output_config"] == {"effort": "low"}
    assert "output_config" not in haiku  # Haiku 4.5 does not take `effort`
    assert not ({"temperature", "top_p", "top_k", "thinking"} & set(sonnet))


def test_model_names_come_from_the_environment(db, monkeypatch):
    conn, _, _ = db
    monkeypatch.setenv("AEROTRACE_MODEL", "my-big-model")
    monkeypatch.setenv("AEROTRACE_FAST_MODEL", "my-fast-model")
    ex = Explainer(conn, FakeLLM(), log=lambda *_: None)
    assert (ex.model, ex.fast_model) == ("my-big-model", "my-fast-model")
    monkeypatch.delenv("AEROTRACE_MODEL")
    monkeypatch.delenv("AEROTRACE_FAST_MODEL")
    ex = Explainer(conn, FakeLLM(), log=lambda *_: None)
    assert (ex.model, ex.fast_model) == (explain.DEFAULT_MODEL, explain.DEFAULT_FAST_MODEL)


def test_openai_compatible_request_shape():
    seen = []

    class FakeCompletions:
        async def create(self, **kw):
            seen.append(kw)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"short": "x"}'))],
                usage=SimpleNamespace(prompt_tokens=13, completion_tokens=4),
            )

    llm = explain.OpenAICompatibleLLM(client=SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())))
    msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "{}"}, {"role": "user", "content": "fix"}]
    res = asyncio.run(llm.complete(model="qwen3-235b-a22b-instruct-2507", system="sys", messages=msgs, max_tokens=500))
    assert (res.text, res.input_tokens, res.output_tokens) == ('{"short": "x"}', 13, 4)
    (kw,) = seen
    assert kw["model"] == "qwen3-235b-a22b-instruct-2507" and kw["max_tokens"] == 500
    assert kw["messages"] == [{"role": "system", "content": "sys"}, *msgs]  # system prompt goes first, as a message


def test_provider_follows_the_keys_unless_told(monkeypatch):
    assert explain.provider() == "anthropic" and not explain.has_credentials()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert explain.provider() == "openai" and explain.has_credentials()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    assert explain.provider() == "anthropic"  # both keys: Anthropic, unless told otherwise
    monkeypatch.setenv("AEROTRACE_PROVIDER", "openai")
    assert explain.provider() == "openai"


def test_env_file_is_read_but_never_overrides_the_shell(tmp_path, monkeypatch):
    from extractor.common.config import load_env_file

    env = tmp_path / ".env"
    env.write_text('# comment\nexport AT_ONE="quoted value"\nAT_TWO=plain=with=equals\nAT_SHELL=from-file\n\nnot a line\n')
    monkeypatch.setenv("AT_SHELL", "from-shell")
    for k in ("AT_ONE", "AT_TWO"):
        monkeypatch.delenv(k, raising=False)
    assert load_env_file(env) == ["AT_ONE", "AT_TWO"]
    import os

    assert os.environ["AT_ONE"] == "quoted value" and os.environ["AT_TWO"] == "plain=with=equals"
    assert os.environ["AT_SHELL"] == "from-shell"
    assert load_env_file(tmp_path / "missing") == []
    for k in ("AT_ONE", "AT_TWO"):
        monkeypatch.delenv(k)


# ---------------------------------------------------------------- offline

def test_offline_never_creates_or_calls_the_api_and_keeps_saved_work(db, monkeypatch, capsys):
    conn, path, _ = db
    run(conn, FakeLLM())  # an earlier online run saved explanations
    conn.execute("UPDATE summaries SET prompt_hash = 'stale' WHERE function_id IN (SELECT function_id FROM summaries WHERE model != 'template' LIMIT 2)")
    conn.commit()
    saved = conn.execute("SELECT COUNT(*) FROM summaries WHERE model != 'template'").fetchone()[0]
    conn.close()

    def boom(*a, **k):
        raise AssertionError("the real client must not even be built when offline")

    monkeypatch.setattr(explain, "AnthropicLLM", boom)
    monkeypatch.setenv("AEROTRACE_OFFLINE", "1")
    assert explain.main([path.stem, "--data-dir", str(path.parent)]) == 0  # no --yes needed: it costs nothing
    out = capsys.readouterr().out
    assert "Offline:" in out and "skipped because offline" in out
    c2 = store.connect(path)
    assert c2.execute("SELECT COUNT(*) FROM summaries WHERE model != 'template'").fetchone()[0] == saved  # nothing lost
    assert c2.execute("SELECT COUNT(*) FROM summaries WHERE model = 'template'").fetchone()[0] >= 3  # templates still written
    c2.close()


def test_offline_flag_works_without_the_environment_variable(db, monkeypatch):
    conn, path, _ = db
    conn.close()
    monkeypatch.delenv("AEROTRACE_OFFLINE", raising=False)
    monkeypatch.setattr(explain, "AnthropicLLM", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no client")))
    assert explain.main([path.stem, "--data-dir", str(path.parent), "--offline"]) == 0


def test_server_reports_offline_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("AEROTRACE_OFFLINE", "1")
    assert TestClient(create_app(tmp_path)).get("/api/status").json() == {"offline": True}
    monkeypatch.delenv("AEROTRACE_OFFLINE")
    assert TestClient(create_app(tmp_path)).get("/api/status").json() == {"offline": False}
