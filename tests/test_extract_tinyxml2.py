"""End-to-end check of the C++ extractor on a real repo (tinyxml2)."""

import subprocess
from pathlib import Path

import pytest

from extractor.common.schema import FunctionNode, load_graph, save_graph
from extractor.common.enrich import enrich_graph
from extractor.cpp.build import extract_repo

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "tinyxml2"

# Every function definition (and pure-virtual declaration) is its own node: 416 today.
MIN_FUNCTIONS, MAX_FUNCTIONS = 380, 440


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    if not (REPO / "tinyxml2.cpp").exists():
        r = subprocess.run(
            ["git", "clone", "--depth", "1", "https://github.com/leethomason/tinyxml2", str(REPO)],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            pytest.skip(f"could not clone tinyxml2: {r.stderr[-200:]}")
    cache = tmp_path_factory.mktemp("cache")
    graph, report = extract_repo(REPO, cache_dir=cache, jobs=2, log=lambda *_: None)
    enrich_graph(graph)
    return graph, report


@pytest.fixture(scope="module")
def graph(result):
    return result[0]


def functions(graph, suffix):
    return [
        n for n in graph.nodes if isinstance(n, FunctionNode) and n.qualified_name.endswith(suffix)
    ]


def names(graph):
    return {n.id: n.qualified_name for n in graph.nodes if hasattr(n, "qualified_name")}


def targets(graph, src_suffix, kinds):
    nm = names(graph)
    src_ids = {n.id for n in functions(graph, src_suffix)}
    return {
        (e.kind, nm[e.target]) for e in graph.edges if e.source in src_ids and e.kind in kinds
    }


def test_no_parse_errors(result):
    assert result[1].error_count == 0


def test_function_count(graph):
    n = sum(1 for x in graph.nodes if isinstance(x, FunctionNode))
    assert MIN_FUNCTIONS <= n <= MAX_FUNCTIONS, n


def test_no_duplicate_ids(graph):
    ids = [n.id for n in graph.nodes]
    assert len(ids) == len(set(ids))


def test_no_dangling_edges(graph):
    ids = {n.id for n in graph.nodes}
    assert all(e.source in ids and e.target in ids for e in graph.edges)


def test_loadfile_calls_parse(graph):
    got = targets(graph, "XMLDocument::LoadFile", {"calls", "may_call"})
    assert any(t.endswith("XMLDocument::Parse") for _, t in got)


def test_parse_calls_parsedeep(graph):
    # ParseDeep is virtual, so the schema records this as may_call (it dispatches).
    got = targets(graph, "XMLDocument::Parse", {"calls", "may_call"})
    assert any(t.endswith("XMLNode::ParseDeep") for _, t in got)


def test_unlink_writes_parent_and_next(graph):
    got = {t for k, t in targets(graph, "XMLNode::Unlink", {"writes"})}
    assert any(t.endswith("XMLNode::_parent") for t in got)
    assert any(t.endswith("XMLNode::_next") for t in got)


def test_errorid_reads_but_does_not_write(graph):
    reads = {t for _, t in targets(graph, "XMLDocument::ErrorID", {"reads"})}
    writes = {t for _, t in targets(graph, "XMLDocument::ErrorID", {"writes"})}
    assert any(t.endswith("XMLDocument::_errorID") for t in reads)
    assert not any(t.endswith("XMLDocument::_errorID") for t in writes)


def test_save_and_load_extracted_graph(graph, tmp_path):
    save_graph(graph, tmp_path / "g.json")
    assert load_graph(tmp_path / "g.json") == graph


def test_parsedeep_may_call_overrides(graph):
    got = {t for _, t in targets(graph, "XMLNode::ParseDeep", {"may_call"})}
    assert "tinyxml2::XMLElement::ParseDeep" in got
    assert "tinyxml2::XMLText::ParseDeep" in got


def test_element_accept_overrides_node_accept(graph):
    got = {t for _, t in targets(graph, "XMLElement::Accept", {"overrides"})}
    assert "tinyxml2::XMLNode::Accept" in got
