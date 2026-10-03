from datetime import datetime, timezone

from extractor.common.schema import (
    Edge,
    FieldNode,
    FunctionNode,
    Graph,
    load_graph,
    save_graph,
)


def build_graph() -> Graph:
    run = FunctionNode(
        id="c:@N@JSBSim@S@FGFDMExec@F@Run#",
        name="Run",
        qualified_name="JSBSim::FGFDMExec::Run",
        container="JSBSim::FGFDMExec",
        language="cpp",
        file="src/FGFDMExec.cpp",
        line_start=10,
        line_end=40,
        signature="bool Run()",
        is_virtual=False,
        is_entry_point=True,
        loc=28,
        doc_comment="Runs one simulation step.",
    )
    step = FunctionNode(
        id="c:@N@JSBSim@S@FGFDMExec@F@Step#",
        name="Step",
        qualified_name="JSBSim::FGFDMExec::Step",
        container="JSBSim::FGFDMExec",
        language="cpp",
        file="src/FGFDMExec.cpp",
        line_start=50,
        line_end=60,
        signature="void Step()",
        loc=9,
    )
    field = FieldNode(
        id="c:@N@JSBSim@S@FGFDMExec@FI@Frame",
        name="Frame",
        qualified_name="JSBSim::FGFDMExec::Frame",
        container="JSBSim::FGFDMExec",
        type="unsigned int",
        file="src/FGFDMExec.h",
        line=22,
    )
    graph = Graph(
        repo_name="tiny",
        repo_url="https://example.com/tiny.git",
        commit_sha="abc123",
        language="cpp",
        extracted_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        nodes=[run, step, field],
        edges=[
            Edge(source=run.id, target=step.id, kind="calls", count=1, lines=[15]),
            Edge(source=step.id, target=field.id, kind="writes", count=2, lines=[52, 55]),
        ],
    )
    graph.refresh_stats()
    return graph


def test_stats_counts():
    g = build_graph()
    assert g.stats.nodes == {"function": 2, "field": 1, "file": 0}
    assert g.stats.edges["calls"] == 1
    assert g.stats.edges["writes"] == 1
    assert g.stats.edges["reads"] == 0


def test_round_trip_exact(tmp_path):
    original = build_graph()
    path = tmp_path / "graph.json"
    save_graph(original, path)
    loaded = load_graph(path)
    assert loaded == original
    assert loaded.model_dump_json() == original.model_dump_json()
