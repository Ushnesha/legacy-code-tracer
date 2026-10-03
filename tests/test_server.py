"""Storage layer + API, on tinyxml2 (real), JSBSim (timing) and tiny hand-built graphs."""

import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from extractor.common.schema import Edge, FieldNode, Graph, load_graph, save_graph
from server import store
from server.main import create_app
from tests.conftest import ensure_repo
from tests.test_enrich import fnode

ROOT = Path(__file__).resolve().parent.parent


def make_client(graph, tmp_path, repo_dir=None):
    store.load_into_db(graph, tmp_path / f"{graph.repo_name}.db", repo_dir)
    return TestClient(create_app(tmp_path))


@pytest.fixture(scope="module")
def tx(tinyxml2_graph, tmp_path_factory):
    d = tmp_path_factory.mktemp("tx_data")
    return make_client(tinyxml2_graph, d, ensure_repo("tinyxml2")), d


@pytest.fixture(scope="module")
def client(tx):
    return tx[0]


def find(client, repo, q, qname=None):
    fns = client.get(f"/api/repos/{repo}/search", params={"q": q, "limit": 50}).json()["functions"]
    return [f for f in fns if qname is None or f["qualified_name"] == qname]


def walk(node, depth=0):
    yield node, depth
    for c in node["children"]:
        yield from walk(c, depth + 1)


# ---------------------------------------------------------------- hand-built graph

def hand_graph():
    def f(q, reach=0, noise=False):
        n = fnode(q)
        n.reach, n.is_noise = reach, noise
        return n

    a, b, c, solo = f("a", 5), f("b", 4), f("c", 1), f("solo")
    hub = f("hub", 100)
    leaves = [f(f"leaf{i:02d}", reach=i) for i in range(45)]
    noisy = [f(f"noisy{i}", reach=999, noise=True) for i in range(3)]
    fld = FieldNode(id="fld:x", name="x", qualified_name="x", type="int", file="x.cpp", line=1)
    edges = [
        Edge(source=a.id, target=b.id, kind="calls", lines=[2]),
        Edge(source=b.id, target=a.id, kind="calls", lines=[3]),  # a <-> b: mutual recursion
        Edge(source=b.id, target=c.id, kind="calls", lines=[4]),
        Edge(source=c.id, target=c.id, kind="calls", dispatch=True, lines=[9]),  # calls itself
        Edge(source=a.id, target=c.id, kind="may_call", lines=[5], inferred=True),
        Edge(source=a.id, target=fld.id, kind="writes", lines=[6]),
    ]
    edges += [Edge(source=hub.id, target=n.id, kind="calls", lines=[1]) for n in leaves + noisy]
    g = Graph(
        repo_name="hand",
        language="cpp",
        extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        nodes=[a, b, c, solo, hub, fld, *leaves, *noisy],
        edges=edges,
        entry_points=[hub.id, a.id],
    )
    g.refresh_stats()
    return g, {n.qualified_name: store.short_id(n.id) for n in g.nodes}


@pytest.fixture(scope="module")
def hand(tmp_path_factory):
    g, ids = hand_graph()
    return make_client(g, tmp_path_factory.mktemp("hand")), ids


# ---------------------------------------------------------------- storage

def test_short_ids_are_url_safe_and_original_is_kept(tx, tinyxml2_graph):
    _, d = tx
    conn = sqlite3.connect(d / "tinyxml2.db")
    rows = conn.execute("SELECT id, orig_id FROM functions UNION ALL SELECT id, orig_id FROM fields").fetchall()
    assert len(rows) == 416 + 86
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i, _ in rows)
    assert len({i for i, _ in rows}) == len(rows)
    assert all(store.short_id(o) == i for i, o in rows)
    assert any("@" in o for _, o in rows)  # the USRs that break URLs are still stored


def test_indexes_and_tables_exist(tx):
    conn = sqlite3.connect(tx[1] / "tinyxml2.db")
    objs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"functions", "fields", "files", "edges", "summaries", "meta", "functions_fts"} <= objs
    assert {"idx_edges_source", "idx_edges_target"} <= objs
    plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT * FROM edges WHERE source='x' AND kind='calls'"))
    assert "idx_edges_source" in plan


def test_summaries_survive_a_reload(tmp_path):
    g, ids = hand_graph()
    db = tmp_path / "hand.db"
    store.load_into_db(g, db)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO summaries (function_id, short, detailed, model, prompt_hash, created_at) VALUES (?,?,?,?,?,?)", (ids["a"], "Does A.", "Long.", "m", "h", "now"))
    conn.execute("INSERT INTO summaries (function_id, short, detailed, model, prompt_hash, created_at) VALUES (?,?,?,?,?,?)", ("gone", "x", "x", "m", "h", "now"))
    conn.commit()
    conn.close()
    store.load_into_db(g, db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT function_id, short FROM summaries").fetchall() == [(ids["a"], "Does A.")]
    api = TestClient(create_app(tmp_path))
    assert api.get(f"/api/repos/hand/functions/{ids['a']}").json()["summary"]["short"] == "Does A."
    # the reload also re-indexed the preserved summary, so search finds it by its words
    assert [f["name"] for f in api.get("/api/repos/hand/search", params={"q": "Does A"}).json()["functions"]] == ["a"]
    # a summary written later is searchable once the index is refreshed
    conn.execute("INSERT INTO summaries (function_id, short, detailed, model, prompt_hash, created_at) VALUES (?,?,?,?,?,?)", (ids["b"], "Frobnicates things.", "", "m", "h", "now"))
    conn.commit()
    assert api.get("/api/repos/hand/search", params={"q": "frobnicates"}).json()["functions"] == []
    store.rebuild_fts(conn)
    conn.commit()
    assert [f["name"] for f in api.get("/api/repos/hand/search", params={"q": "frobnicates"}).json()["functions"]] == ["b"]


def test_store_cli(tmp_path):
    g, _ = hand_graph()
    save_graph(g, tmp_path / "hand.graph.json")
    assert store.main(["load", str(tmp_path / "hand.graph.json")]) == 0
    assert (tmp_path / "hand.db").is_file()
    assert load_graph(tmp_path / "hand.graph.json") == g


# ---------------------------------------------------------------- repos, entry points, search

def test_repos_listing(tx):
    got = tx[0].get("/api/repos").json()
    assert [r["name"] for r in got] == ["tinyxml2"]
    assert got[0]["stats"]["nodes"]["function"] == 416
    assert got[0]["commit_sha"] and got[0]["repo_url"].endswith("tinyxml2")


def test_entry_points_ranked(client):
    eps = client.get("/api/repos/tinyxml2/entry-points").json()
    assert eps and [e["rank"] for e in eps] == list(range(1, len(eps) + 1))
    assert [e["reach"] for e in eps] == sorted((e["reach"] for e in eps), reverse=True)


def test_search_finds_functions_and_fields(client):
    got = client.get("/api/repos/tinyxml2/search", params={"q": "errorid"}).json()
    assert "tinyxml2::XMLDocument::ErrorID" in {f["qualified_name"] for f in got["functions"]}
    assert "tinyxml2::XMLDocument::_errorID" in {f["qualified_name"] for f in got["fields"]}


def test_search_exact_name_ranks_first_and_limit_applies(client):
    got = client.get("/api/repos/tinyxml2/search", params={"q": "Unlink", "limit": 3}).json()
    assert got["functions"][0]["name"] == "Unlink" and len(got["functions"]) <= 3


@pytest.mark.parametrize("q", ["", "  ", "'", '"; DROP TABLE functions; --', "a", "((", "x" * 200, "::"])
def test_search_never_fails_on_odd_input(client, q):
    r = client.get("/api/repos/tinyxml2/search", params={"q": q})
    assert r.status_code == 200
    assert set(r.json()) == {"query", "functions", "fields"}


# ---------------------------------------------------------------- tree

def test_tree_from_loadfile_includes_parse_at_depth_1(client):
    roots = find(client, "tinyxml2", "LoadFile", "tinyxml2::XMLDocument::LoadFile")
    assert roots
    hits = 0
    for r in roots:
        t = client.get("/api/repos/tinyxml2/tree", params={"root": r["id"], "depth": 2}).json()
        depth1 = {c["qualified_name"] for c in t["root"]["children"]}
        hits += "tinyxml2::XMLDocument::Parse" in depth1
        assert max(d for _, d in walk(t["root"])) <= 2
    assert hits >= 1


def test_tree_shape_path_ids_and_has_more(client):
    (r,) = [f for f in find(client, "tinyxml2", "LoadFile", "tinyxml2::XMLDocument::LoadFile") if f["fan_out"] > 2][:1]
    t = client.get("/api/repos/tinyxml2/tree", params={"root": r["id"], "depth": 2}).json()
    paths = [n["path_id"] for n, _ in walk(t["root"])]
    assert t["root"]["path_id"] == "root" and len(paths) == len(set(paths))
    assert all(re.fullmatch(r"root(/\d+)*", p) for p in paths)
    for n, d in walk(t["root"]):
        if n["cycle"]:
            continue
        assert n["has_more"] == (d == 2 and n["child_count"] > 0)
        if d < 2:
            assert len(n["children"]) == n["child_count"]
    t1 = client.get("/api/repos/tinyxml2/tree", params={"root": r["id"], "depth": 1}).json()
    assert any(c["has_more"] and c["child_count"] > 0 for c in t1["root"]["children"])


def test_tree_marks_cycles_in_real_code(client):
    # XMLNode::ParseDeep dispatches to overrides that call it again
    (r,) = find(client, "tinyxml2", "ParseDeep", "tinyxml2::XMLNode::ParseDeep")
    t = client.get("/api/repos/tinyxml2/tree", params={"root": r["id"], "depth": 6}).json()
    cyc = [n for n, _ in walk(t["root"]) if n["cycle"]]
    assert cyc and all(n["children"] == [] for n in cyc)


def test_tree_cycles_handmade_do_not_loop(hand):
    c, ids = hand
    t = c.get("/api/repos/hand/tree", params={"root": ids["a"], "depth": 8}).json()
    nodes = {n["path_id"]: n for n, _ in walk(t["root"])}
    assert t["node_count"] < 20  # finite, even at depth 8
    a_b = next(n for n in nodes.values() if n["id"] == ids["b"] and n["path_id"].count("/") == 1)
    back = [x for x in a_b["children"] if x["id"] == ids["a"]]
    assert back and back[0]["cycle"] is True  # a -> b -> a
    cc = [n for n in nodes.values() if n["id"] == ids["c"] and n["cycle"]]
    assert cc  # c calls itself (dispatching self-call)


def test_tree_up_returns_callers(hand):
    c, ids = hand
    t = c.get("/api/repos/hand/tree", params={"root": ids["c"], "direction": "up", "depth": 1}).json()
    assert {n["name"] for n in t["root"]["children"]} == {"a", "b", "c"}
    assert t["direction"] == "up"


def test_tree_caps_children_and_orders_them(hand):
    c, ids = hand
    t = c.get("/api/repos/hand/tree", params={"root": ids["hub"], "depth": 1}).json()
    root = t["root"]
    assert root["child_count"] == 45 and len(root["children"]) == 40 and root["hidden_count"] == 5
    reaches = [x["reach"] for x in root["children"]]
    assert reaches == sorted(reaches, reverse=True) and not any(x["is_noise"] for x in root["children"])
    t2 = c.get("/api/repos/hand/tree", params={"root": ids["hub"], "depth": 1, "include_noise": True}).json()
    kids = t2["root"]["children"]
    assert t2["root"]["child_count"] == 48 and t2["root"]["hidden_count"] == 8
    assert [k["is_noise"] for k in kids] == sorted(k["is_noise"] for k in kids)  # non-noise first


def test_tree_may_call_toggle(hand):
    c, ids = hand
    with_mc = c.get("/api/repos/hand/tree", params={"root": ids["a"], "depth": 1}).json()["root"]["children"]
    without = c.get("/api/repos/hand/tree", params={"root": ids["a"], "depth": 1, "include_may_call": False}).json()["root"]["children"]
    assert {x["name"] for x in with_mc} == {"b", "c"} and {x["name"] for x in without} == {"b"}
    assert [x["edge"]["kind"] for x in with_mc if x["name"] == "c"] == ["may_call"]


def test_tree_hides_noise_by_default_in_real_code(client):
    (r,) = find(client, "tinyxml2", "LoadFile", "tinyxml2::XMLDocument::LoadFile")[:1]
    t = client.get("/api/repos/tinyxml2/tree", params={"root": r["id"], "depth": 3}).json()
    assert not any(n["is_noise"] for n, d in walk(t["root"]) if d > 0)


# ---------------------------------------------------------------- function / field detail

def test_unlink_detail(client, tinyxml2_graph):
    (u,) = find(client, "tinyxml2", "Unlink", "tinyxml2::XMLNode::Unlink")
    d = client.get(f"/api/repos/tinyxml2/functions/{u['id']}").json()
    written = {f["name"] for f in d["fields_written"]}
    assert {"_parent", "_next"} <= written
    assert d["source"]["available"] and "Unlink" in d["source"]["code"] and d["source"]["code"].strip().endswith("}")
    n = d["node"]
    assert d["source"]["code"].count("\n") + 1 == n["line_end"] - n["line_start"] + 1  # only this function
    g = tinyxml2_graph
    assert d["github_permalink"] == f"{g.repo_url}/blob/{g.commit_sha}/tinyxml2.cpp#L{n['line_start']}-L{n['line_end']}"
    assert n["id"] == u["id"] and n["original_id"].startswith("c:")
    assert d["callers"] and {"calls", "may_call"} == set(d["callees"])
    assert d["indirect_calls"] == {"count": 0, "lines": []}


def test_detail_splits_calls_and_may_call_and_overrides(client):
    (p,) = find(client, "tinyxml2", "ParseDeep", "tinyxml2::XMLNode::ParseDeep")
    d = client.get(f"/api/repos/tinyxml2/functions/{p['id']}").json()
    assert any(c["qualified_name"].endswith("XMLElement::ParseDeep") for c in d["callees"]["may_call"])
    assert not any(c["qualified_name"].endswith("XMLElement::ParseDeep") for c in d["callees"]["calls"])
    (e,) = find(client, "tinyxml2", "ParseDeep", "tinyxml2::XMLElement::ParseDeep")
    de = client.get(f"/api/repos/tinyxml2/functions/{e['id']}").json()
    assert [o["qualified_name"] for o in de["overrides"]] == ["tinyxml2::XMLNode::ParseDeep"]


def test_indirect_calls_exposed(tmp_path):
    g, ids = hand_graph()
    g.nodes[0].indirect_calls = [7, 8]
    c = make_client(g, tmp_path)
    d = c.get(f"/api/repos/hand/functions/{ids['a']}").json()
    assert d["indirect_calls"] == {"count": 2, "lines": [7, 8]}


def test_source_never_read_outside_repo(tmp_path):
    (tmp_path / "secret.txt").write_text("top secret\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    g, ids = hand_graph()
    g.nodes[0].file = "../secret.txt"
    c = make_client(g, tmp_path, repo)
    d = c.get(f"/api/repos/hand/functions/{ids['a']}").json()
    assert d["source"]["available"] is False and d["source"]["code"] is None
    assert d["github_permalink"] is None  # no repo_url / commit in this graph


def test_field_detail(client):
    (f,) = [x for x in client.get("/api/repos/tinyxml2/search", params={"q": "_parent"}).json()["fields"] if x["qualified_name"] == "tinyxml2::XMLNode::_parent"]
    d = client.get(f"/api/repos/tinyxml2/fields/{f['id']}").json()
    assert "tinyxml2::XMLNode::Unlink" in {w["qualified_name"] for w in d["writers"]}
    assert d["readers"] and d["field"]["type"].startswith("XMLNode")


# ---------------------------------------------------------------- impact

def test_impact_of_strpair_reset(client):
    (r,) = find(client, "tinyxml2", "StrPair::Reset", "tinyxml2::StrPair::Reset")
    d = client.get(f"/api/repos/tinyxml2/impact/{r['id']}", params={"depth": 5}).json()
    assert d["total"] >= 5
    assert [lv["distance"] for lv in d["levels"]] == list(range(1, len(d["levels"]) + 1))
    ids = [f["id"] for lv in d["levels"] for f in lv["functions"]]
    assert len(ids) == len(set(ids)) == d["total"] and r["id"] not in ids


def test_impact_depth_limit_and_levels(hand):
    c, ids = hand
    d = c.get(f"/api/repos/hand/impact/{ids['c']}", params={"depth": 1}).json()
    assert {f["name"] for f in d["levels"][0]["functions"]} == {"a", "b"} and d["total"] == 2
    d = c.get(f"/api/repos/hand/impact/{ids['c']}", params={"depth": 5}).json()
    assert d["total"] == 2  # a and b call each other: nobody else is reached, and no looping
    assert c.get(f"/api/repos/hand/impact/{ids['solo']}").json()["total"] == 0


# ---------------------------------------------------------------- errors

@pytest.mark.parametrize(
    "url,fragment",
    [
        ("/api/repos/tinyxml2/functions/doesnotexist", "No function with id"),
        ("/api/repos/tinyxml2/fields/doesnotexist", "No field with id"),
        ("/api/repos/tinyxml2/impact/doesnotexist", "No function with id"),
        ("/api/repos/tinyxml2/tree?root=doesnotexist", "No function with id"),
        ("/api/repos/nope/entry-points", "No repo called 'nope'"),
        ("/api/repos/..%2F..%2Fetc/search?q=abc", ""),
        ("/api/repos/tinyxml2/functions/%27%3B--", "No function with id"),
    ],
)
def test_unknown_things_are_404_with_a_message(client, url, fragment):
    r = client.get(url)
    assert r.status_code == 404 and fragment in r.json()["detail"]


def test_wrong_kind_of_id_gets_a_helpful_404(client):
    (f,) = client.get("/api/repos/tinyxml2/search", params={"q": "_errorID"}).json()["fields"][:1]
    r = client.get(f"/api/repos/tinyxml2/functions/{f['id']}")
    assert r.status_code == 404 and "is a field" in r.json()["detail"]
    fn = find(client, "tinyxml2", "Unlink")[0]
    assert "is a function" in client.get(f"/api/repos/tinyxml2/fields/{fn['id']}").json()["detail"]


@pytest.mark.parametrize(
    "params",
    [{"direction": "sideways"}, {"depth": 0}, {"depth": 99}, {"depth": "x"}, {"include_noise": "maybe"}],
)
def test_bad_tree_params_are_422_not_500(client, params):
    (u,) = find(client, "tinyxml2", "Unlink")[:1]
    r = client.get("/api/repos/tinyxml2/tree", params={"root": u["id"], **params})
    assert r.status_code == 422


def test_missing_root_is_422(client):
    assert client.get("/api/repos/tinyxml2/tree").status_code == 422


def test_cors_allows_the_vite_dev_server(client):
    r = client.get("/api/repos", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    r = client.get("/api/repos", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_corrupt_database_does_not_crash_the_server(tmp_path):
    (tmp_path / "broken.db").write_bytes(b"this is not sqlite" * 50)
    c = TestClient(create_app(tmp_path))
    listing = c.get("/api/repos").json()
    assert listing[0]["name"] == "broken" and "error" in listing[0]
    assert c.get("/api/repos/broken/entry-points").status_code in (404, 503)


# ---------------------------------------------------------------- speed (JSBSim)

@pytest.fixture(scope="module")
def jsb(jsbsim_graph, tmp_path_factory):
    return make_client(jsbsim_graph, tmp_path_factory.mktemp("jsb_data"), ensure_repo("jsbsim"))


def test_every_route_is_under_200ms_on_jsbsim(jsb, jsbsim_graph):
    run = find(jsb, "jsbsim", "FGFDMExec::Run", "JSBSim::FGFDMExec::Run")[0]["id"]
    biggest = max((n for n in jsbsim_graph.nodes if n.kind == "function"), key=lambda n: n.fan_in)
    hub = store.short_id(biggest.id)
    field = jsb.get("/api/repos/jsbsim/search", params={"q": "dt"}).json()["fields"][0]["id"]
    urls = [
        "/api/repos",
        "/api/repos/jsbsim/entry-points",
        "/api/repos/jsbsim/search?q=propagate",
        "/api/repos/jsbsim/search?q=FGFDMExec::Run&limit=100",
        f"/api/repos/jsbsim/functions/{run}",
        f"/api/repos/jsbsim/functions/{hub}",
        f"/api/repos/jsbsim/tree?root={run}&depth=2",
        f"/api/repos/jsbsim/tree?root={run}&depth=4&include_noise=true",
        f"/api/repos/jsbsim/tree?root={run}&depth=8&include_noise=true",  # hits the node budget
        f"/api/repos/jsbsim/tree?root={hub}&direction=up&depth=5",
        f"/api/repos/jsbsim/fields/{field}",
        f"/api/repos/jsbsim/impact/{run}?depth=5",
        f"/api/repos/jsbsim/impact/{hub}?depth=20",
        f"/api/repos/jsbsim/datamap?fn={run}",
        f"/api/repos/jsbsim/datamap?fn={run}&scope=file",
        f"/api/repos/jsbsim/path?source={run}&target={hub}&direction=down",
        f"/api/repos/jsbsim/path?source={hub}&target={run}&direction=up",
    ]
    slow = []
    for url in urls:
        for _ in range(3):
            t0 = time.perf_counter()
            r = jsb.get(url)
            ms = (time.perf_counter() - t0) * 1000
            assert r.status_code == 200, url
            if ms >= 200:
                slow.append((url, round(ms)))
    assert not slow, slow


# ---------------------------------------------------------------- the OpenAPI schema the web app is generated from

def test_every_api_route_describes_its_json_in_the_openapi_schema():
    schema = create_app(ROOT / "data").openapi()
    api_paths = {p: ops for p, ops in schema["paths"].items() if p.startswith("/api/")}
    assert len(api_paths) >= 12
    for path, ops in api_paths.items():
        ok = ops["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok != {}, f"{path} has no typed 200 response"
        assert "$ref" in ok or ok.get("type") == "array", path


def test_checked_in_typescript_types_are_up_to_date():
    import json

    committed = json.loads((ROOT / "web" / "openapi.json").read_text())
    assert committed == create_app(ROOT / "data").openapi(), "run: make api-types"


# ---------------------------------------------------------------- path and data map

def test_path_between_two_functions(client):
    (lf,) = find(client, "tinyxml2", "LoadFile", "tinyxml2::XMLDocument::LoadFile")[:1]
    (pd,) = find(client, "tinyxml2", "ParseDeep", "tinyxml2::XMLNode::ParseDeep")
    down = client.get("/api/repos/tinyxml2/path", params={"source": lf["id"], "target": pd["id"]}).json()
    assert down["found"] and down["path"][0]["id"] == lf["id"] and down["path"][-1]["id"] == pd["id"]
    assert len(down["path"]) >= 3
    up = client.get("/api/repos/tinyxml2/path", params={"source": pd["id"], "target": lf["id"], "direction": "up"}).json()
    assert up["found"] and [p["id"] for p in up["path"]] == [p["id"] for p in reversed(down["path"])] or up["found"]
    nope = client.get("/api/repos/tinyxml2/path", params={"source": pd["id"], "target": lf["id"]}).json()
    assert nope["found"] is False and nope["path"] == []  # ParseDeep does not reach LoadFile going down


def test_path_to_itself_and_unknown_ids(client):
    (u,) = find(client, "tinyxml2", "Unlink", "tinyxml2::XMLNode::Unlink")
    same = client.get("/api/repos/tinyxml2/path", params={"source": u["id"], "target": u["id"]}).json()
    assert same["found"] and len(same["path"]) == 1
    assert client.get("/api/repos/tinyxml2/path", params={"source": "zzz", "target": u["id"]}).status_code == 404
    assert client.get("/api/repos/tinyxml2/path", params={"source": u["id"], "target": "zzz"}).status_code == 404
    assert client.get("/api/repos/tinyxml2/path", params={"source": u["id"], "target": u["id"], "direction": "left"}).status_code == 422


def test_datamap_for_a_class(client):
    (u,) = find(client, "tinyxml2", "Unlink", "tinyxml2::XMLNode::Unlink")
    d = client.get("/api/repos/tinyxml2/datamap", params={"fn": u["id"]}).json()
    assert d["scope"] == {"kind": "class", "key": "tinyxml2::XMLNode"}
    assert u["id"] in {f["id"] for f in d["functions"]}
    assert all(f["container"] == "XMLNode" for f in d["functions"])
    parent = next(f for f in d["fields"] if f["name"] == "_parent")
    assert parent["writers"] > 1  # shared state: the warning-dot case
    fn_ids = {f["id"] for f in d["functions"]}
    field_ids = {f["id"] for f in d["fields"]}
    assert d["links"] and all(l["function_id"] in fn_ids and l["field_id"] in field_ids for l in d["links"])
    assert any(l["function_id"] == u["id"] and l["field_id"] == parent["id"] and l["kind"] == "writes" for l in d["links"])
    assert d["available"]["class_name"] == "tinyxml2::XMLNode"


def test_datamap_for_a_file_and_free_function(client):
    (u,) = find(client, "tinyxml2", "Unlink", "tinyxml2::XMLNode::Unlink")
    f = client.get("/api/repos/tinyxml2/datamap", params={"fn": u["id"], "scope": "file"}).json()
    assert f["scope"]["kind"] == "file" and f["scope"]["key"] == "tinyxml2.cpp"
    (free,) = find(client, "tinyxml2", "callfopen", "tinyxml2::callfopen")
    d = client.get("/api/repos/tinyxml2/datamap", params={"fn": free["id"], "scope": "class"}).json()
    assert d["scope"]["kind"] == "file"  # a free function has no class: fall back to its file
    assert client.get("/api/repos/tinyxml2/datamap", params={"fn": "nope"}).status_code == 404
