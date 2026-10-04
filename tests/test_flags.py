"""Review flags: places where the graph is incomplete, worked out from the graph alone."""

from __future__ import annotations

import sqlite3

from extractor.common.flags import MANY_EXTERNAL_CALLS, function_flags
from server import store
from tests.test_server import hand_graph, make_client


def codes(node, may=0):
    return [f["code"] for f in function_flags(node, may)]


def test_a_fully_resolved_function_has_no_flags():
    assert function_flags({"indirect_calls": [], "unresolved_calls": 0, "external_calls": 2}) == []


def test_calls_through_a_pointer_need_review_and_point_at_their_lines():
    (f,) = function_flags({"indirect_calls": [9, 4]})
    assert f["code"] == "indirect_calls" and f["severity"] == "review" and f["lines"] == [4, 9]
    assert "2 calls through a pointer" in f["message"]


def test_unresolved_calls_need_review():
    (f,) = function_flags({"unresolved_calls": 1})
    assert f["code"] == "unresolved_calls" and f["severity"] == "review" and "1 call goes" in f["message"]


def test_notes_for_dispatch_external_recursive_and_bodiless():
    got = codes(
        {"external_calls": MANY_EXTERNAL_CALLS, "recursive": True, "is_pure_virtual": True}, may=3
    )
    assert set(got) == {"virtual_dispatch", "many_external_calls", "recursive", "no_body"}
    assert all(f["severity"] == "note" for f in function_flags({"recursive": True}))
    assert "many_external_calls" not in codes({"external_calls": MANY_EXTERNAL_CALLS - 1})


def test_function_detail_exposes_flags(tmp_path):
    g, ids = hand_graph()
    g.nodes[0].indirect_calls = [7, 8]
    c = make_client(g, tmp_path)
    flagged = c.get(f"/api/repos/hand/functions/{ids['a']}").json()
    by_code = {f["code"]: f for f in flagged["flags"]}
    assert by_code["indirect_calls"]["lines"] == [7, 8]
    clean = c.get(f"/api/repos/hand/functions/{ids['solo']}").json()
    assert clean["flags"] == []


def test_migrate_stops_calling_template_text_verified(tmp_path):
    g, _ = hand_graph()
    make_client(g, tmp_path)
    conn = sqlite3.connect(tmp_path / "hand.db")
    conn.execute("INSERT INTO summaries (function_id, short, model, verified) VALUES ('t', 'x', 'template', 1)")
    conn.execute("INSERT INTO summaries (function_id, short, model, verified) VALUES ('m', 'x', 'some-model', 1)")
    store.migrate(conn)
    got = dict(conn.execute("SELECT function_id, verified FROM summaries"))
    assert got == {"t": None, "m": 1}
