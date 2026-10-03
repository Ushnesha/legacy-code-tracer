"""Extract + enrich a real, large repo (JSBSim)."""

from pathlib import Path

import pytest

from extractor.common.config import find_config
from extractor.common.schema import FunctionNode

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / "repos" / "jsbsim"


@pytest.fixture(scope="module")
def built(jsbsim_graph):
    return jsbsim_graph, find_config("jsbsim", REPO)


def fns(graph, qname):
    return [n for n in graph.nodes if isinstance(n, FunctionNode) and n.qualified_name == qname]


def test_run_may_call_many_model_overrides(built):
    graph, _ = built
    names = {n.id: n.qualified_name for n in graph.nodes if hasattr(n, "qualified_name")}
    (run,) = fns(graph, "JSBSim::FGFDMExec::Run")
    targets = {names[e.target] for e in graph.edges if e.source == run.id and e.kind == "may_call"}
    model_runs = {t for t in targets if t.endswith("::Run") and t != "JSBSim::FGModel::Run"}
    assert len(model_runs) >= 10, sorted(model_runs)
    assert "JSBSim::FGPropagate::Run" in model_runs
    assert "JSBSim::FGAuxiliary::Run" in model_runs


def test_logging_class_is_all_noise(built):
    graph, _ = built
    logging = [n for n in graph.nodes if isinstance(n, FunctionNode) and n.container == "FGLogging"]
    assert logging, "FGLogging should still be in the graph (noise is a tag, not a delete)"
    assert not [n.qualified_name for n in logging if not n.is_noise]
    assert {n.noise_reason for n in logging} <= {"noise_class", "operator"}


def test_fdmexec_run_is_an_entry_point(built):
    graph, cfg = built
    (run,) = fns(graph, "JSBSim::FGFDMExec::Run")
    assert run.is_entry_point
    top10 = graph.entry_points[:10]
    assert run.id in top10 or "JSBSim::FGFDMExec::Run" in cfg.entry_points
    assert run.id in graph.entry_points


def test_entry_points_list_the_configured_ones_first_then_by_reach(built):
    graph, cfg = built
    by_id = {n.id: n for n in graph.nodes if isinstance(n, FunctionNode)}
    named = [i for i in graph.entry_points if any(by_id[i].qualified_name == q for q in cfg.entry_points)]
    assert [by_id[i].qualified_name for i in graph.entry_points[: len(named)]] == [by_id[i].qualified_name for i in named]
    assert len(named) >= 3
    rest = [by_id[i].reach for i in graph.entry_points[len(named) :]]
    assert rest == sorted(rest, reverse=True)


def test_virtual_calls_and_indirect_calls_are_recorded(built):
    graph, _ = built
    assert any(e.kind == "calls" and e.dispatch for e in graph.edges)
    assert any(isinstance(n, FunctionNode) and n.indirect_calls for n in graph.nodes)
