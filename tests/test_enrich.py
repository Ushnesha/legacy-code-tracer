"""The enrich pass, checked on hand-built graphs and a small synthetic C++ repo."""

import textwrap
from datetime import datetime, timezone

import pytest

from extractor.common import enrich
from extractor.common.config import RepoConfig, find_config
from extractor.common.schema import Edge, FunctionNode, Graph, load_graph, save_graph
from extractor.cpp.build import extract_repo


def fnode(qname, params=(), virtual=False, loc=5, const=False, **kw):
    cls, _, name = qname.rpartition("::")
    return FunctionNode(
        id=f"id:{qname}({','.join(params)})",
        name=name,
        qualified_name=qname,
        container=cls.rsplit("::", 1)[-1] if cls else None,
        language="cpp",
        file="x.cpp",
        line_start=1,
        line_end=loc,
        signature=f"void {name}()" + (" const" if const else ""),
        is_virtual=virtual,
        loc=loc,
        param_types=list(params),
        **kw,
    )


def graph_of(nodes, edges, bases=None):
    g = Graph(
        repo_name="t",
        language="cpp",
        extracted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        nodes=nodes,
        edges=edges,
        class_bases=bases or {},
    )
    g.refresh_stats()
    return g


# ---------------------------------------------------------------- hierarchy


@pytest.fixture
def hierarchy():
    a = fnode("A::f", ["int"], virtual=True)
    b = fnode("B::f", ["int"], virtual=True)
    c = fnode("C::f", ["int"], virtual=True)
    d = fnode("D::f", ["double"], virtual=True)  # different parameter types: not an override
    caller = fnode("use")
    g = graph_of(
        [a, b, c, d, caller],
        [Edge(source=caller.id, target=a.id, kind="calls", count=2, lines=[7, 9], dispatch=True)],
        {"A": [], "B": ["A"], "C": ["B"], "D": ["A"]},
    )
    enrich.enrich_graph(g)
    return g, {n.qualified_name: n.id for n in g.nodes}


def pairs(g, kind):
    return {(e.source, e.target) for e in g.edges if e.kind == kind}


def test_overrides_use_nearest_base_and_param_types(hierarchy):
    g, ids = hierarchy
    assert pairs(g, "overrides") == {(ids["B::f"], ids["A::f"]), (ids["C::f"], ids["B::f"])}


def test_may_call_reaches_every_override_transitively(hierarchy):
    g, ids = hierarchy
    assert pairs(g, "may_call") == {(ids["use"], ids["B::f"]), (ids["use"], ids["C::f"])}
    mc = next(e for e in g.edges if e.kind == "may_call" and e.target == ids["C::f"])
    assert mc.lines == [7, 9] and mc.inferred


def test_original_calls_edge_is_kept(hierarchy):
    g, ids = hierarchy
    assert (ids["use"], ids["A::f"]) in pairs(g, "calls")


def test_const_overload_is_not_an_override():
    a = fnode("A::f", [], virtual=True)
    b = fnode("B::f", [], virtual=True, const=True)
    g = graph_of([a, b], [], {"A": [], "B": ["A"]})
    enrich.enrich_graph(g)
    assert pairs(g, "overrides") == set()


def test_rerun_is_idempotent(hierarchy):
    g, _ = hierarchy
    before = g.model_dump()
    enrich.enrich_graph(g)
    assert g.model_dump() == before


# ---------------------------------------------------------------- synthetic repo

CODE = """
struct Cfg {
  int v;
  Cfg() : v(0) {}
  ~Cfg() {}
  int get() const { return v; }
  void set(int x) { v = x; }
  bool operator==(const Cfg& o) const { return v == o.v; }
};
struct Pair { int p, q; void reset() { p = 0; q = 1; } };
struct Util { void log(const char* s); };
void Util::log(const char* s) { (void)s; }
void dbg_dump() {}
int leaf() { return 1; }
int a() { return leaf(); }
int b() { return leaf() + a(); }
void top() { a(); b(); leaf(); }
static void hidden_top() { a(); b(); leaf(); }
int main() { return 0; }
"""


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    repo = tmp_path_factory.mktemp("enrich_repo")
    (repo / "x.cpp").write_text(textwrap.dedent(CODE))
    graph, _ = extract_repo(repo, cache_dir=None, jobs=1, log=lambda *_: None)
    cfg = RepoConfig(noise_classes=["Util"], noise_name_patterns=["dbg_*"], entry_points=["leaf"])
    enrich.enrich_graph(graph, cfg)
    by_name = {}
    for n in graph.nodes:
        if isinstance(n, FunctionNode):
            by_name[n.qualified_name] = n
    return graph, by_name


@pytest.mark.parametrize(
    "qname,reason",
    [
        ("Cfg::get", "accessor"),
        ("Cfg::set", "accessor"),
        ("Cfg::operator==", "operator"),
        ("Cfg::Cfg", "trivial_constructor"),
        ("Cfg::~Cfg", "trivial_destructor"),
        ("Util::log", "noise_class"),
        ("dbg_dump", "name_pattern"),
        ("Pair::reset", None),  # touches two fields: not an accessor
        ("leaf", None),
    ],
)
def test_noise_reasons(synthetic, qname, reason):
    n = synthetic[1][qname]
    assert n.noise_reason == reason and n.is_noise == (reason is not None)


def test_nothing_is_deleted(synthetic):
    graph = synthetic[0]
    assert graph.stats.nodes["function"] == len(synthetic[1])
    assert {"Cfg::get", "Util::log", "dbg_dump"} <= set(synthetic[1])


def test_metrics(synthetic):
    f = synthetic[1]
    assert (f["leaf"].fan_in, f["leaf"].fan_out, f["leaf"].reach) == (4, 0, 0)
    assert (f["a"].fan_in, f["a"].fan_out, f["a"].reach) == (3, 1, 1)
    assert (f["b"].fan_out, f["b"].reach) == (2, 2)
    assert (f["top"].fan_in, f["top"].fan_out, f["top"].reach) == (0, 3, 3)
    assert f["Pair::reset"].fields_written_count == 2
    assert f["a"].fields_written_count == 0


def test_entry_points(synthetic):
    graph, f = synthetic
    assert f["main"].is_entry_point  # main
    assert f["leaf"].is_entry_point  # listed in the config
    assert f["top"].is_entry_point  # public, no callers, 3 outgoing calls
    assert not f["hidden_top"].is_entry_point  # static: not public
    assert not f["a"].is_entry_point
    ids = graph.entry_points
    assert set(ids) == {f[n].id for n in ("main", "leaf", "top")}
    # the one the settings name comes first; the rest follow, biggest reach first
    assert ids == [f["leaf"].id, f["top"].id, f["main"].id]


def test_cli_edits_file_in_place(tmp_path, synthetic):
    graph, _ = synthetic
    fresh = graph.model_copy(deep=True)
    path = tmp_path / "t.graph.json"
    save_graph(fresh, path)
    (tmp_path / "cfg.yml").write_text("noise_classes: [Util]\nnoise_name_patterns: ['dbg_*']\nentry_points: [leaf]\n")
    assert enrich.main([str(path), "--config", str(tmp_path / "cfg.yml")]) == 0
    assert load_graph(path).model_dump() == graph.model_dump()


# ---------------------------------------------------------------- config


def test_config_lookup_order(tmp_path):
    repo = tmp_path / "repo"
    data = tmp_path / "data"
    repo.mkdir()
    data.mkdir()
    (data / "demo.yml").write_text("noise_classes: [FromData]\n")
    assert find_config("demo", repo, data).noise_classes == ["FromData"]
    (repo / "aerotrace.yml").write_text("noise_classes: [FromRepo]\nexclude: [gen/]\n")
    cfg = find_config("demo", repo, data)
    assert cfg.noise_classes == ["FromRepo"] and cfg.exclude == ["gen/"]


def test_config_rejects_unknown_keys(tmp_path):
    p = tmp_path / "bad.yml"
    p.write_text("noise_clases: [X]\n")
    with pytest.raises(ValueError, match="unknown setting"):
        RepoConfig.from_file(p)


def test_jsbsim_config_file_is_valid():
    cfg = find_config("jsbsim")
    assert cfg.noise_classes == ["FGLogging", "FGColumnVector3", "FGMatrix33", "FGQuaternion", "FGLocation"]
    assert cfg.entry_points == [
        "JSBSim::FGFDMExec::Run",
        "JSBSim::FGFDMExec::RunIC",
        "JSBSim::FGFDMExec::LoadModel",
    ]
    assert "src/simgear/" in cfg.exclude and "src/GeographicLib/" in cfg.exclude
