"""Tests for the Ada extractor. libadalang-only tests skip when it is not installed."""

from __future__ import annotations

from pathlib import Path

import pytest

from aerotrace.contracts.models import NormalizedCodeModel
from extractor.ada import lal, treesitter
from extractor.ada.cli import main
from extractor.common.schema import load_graph

HERE = Path(__file__).parent
FIXTURE = HERE / "ada_fixture"
ADL = HERE.parent / "repos" / "ADL"

needs_lal = pytest.mark.skipif(not lal.available(), reason="libadalang not installed")
needs_ts = pytest.mark.skipif(not treesitter.available(), reason="tree-sitter-ada not installed")


def run(repo: Path, out: Path, *extra: str) -> NormalizedCodeModel:
    ncm = out.with_suffix(".ncm.json")
    assert main([str(repo), "--out", str(out), "--ncm-out", str(ncm), *extra]) == 0
    return NormalizedCodeModel.model_validate_json(ncm.read_text())


def edges(m: NormalizedCodeModel, etype: str):
    names = {n.id: n for n in m.nodes}
    return [(names[e.source], names[e.target], e) for e in m.edges if e.type == etype]


@needs_lal
def test_fixture_is_valid_ncm_and_every_node_is_ada(tmp_path):
    m = run(FIXTURE, tmp_path / "g.json")
    assert all(n.properties["language"] == "ada" for n in m.nodes)
    assert all(e.evidence_ids for e in m.edges)
    ev = {e.evidence_id for e in m.evidence}
    assert all(i in ev for e in m.edges for i in e.evidence_ids)
    assert all(c.status == "PARSED" for c in m.coverage)


@needs_lal
def test_out_and_in_out_actuals_are_writes(tmp_path):
    m = run(FIXTURE, tmp_path / "g.json")
    hits = [(s.name, t.qualified_name, e.properties["via"]) for s, t, e in edges(m, "WRITES")]
    assert ("Reset", "Counters.Total", "out") in hits  # Compute (Total) with 'out'
    assert ("Bump", "Counters.Total", ":=") in hits
    # a plain read stays a read
    assert any(s.name == "Compute" and t.name == "Total" for s, t, _ in edges(m, "READS"))


@needs_lal
def test_dispatching_call_has_calls_and_may_call_overrides(tmp_path):
    m = run(FIXTURE, tmp_path / "g.json")
    calls = [(t.signature, e) for s, t, e in edges(m, "CALLS") if s.name == "Draw_All"]
    assert any("S : Shape)" in sig and e.properties["dispatching"] for sig, e in calls)
    may = {t.signature for s, t, _ in edges(m, "MAY_CALL") if s.name == "Draw_All"}
    assert len(may) >= 2 and any("Circle" in x for x in may) and any("Square" in x for x in may)


@needs_lal
def test_access_call_is_counted_not_guessed(tmp_path, capsys):
    m = run(FIXTURE, tmp_path / "g.json")
    unres = edges(m, "UNRESOLVED_CALL")
    assert [(s.name, t.kind) for s, t, _ in unres] == [("Run", "ExternalSymbol")]
    assert "indirect (unresolved) calls: 1" in capsys.readouterr().out


@needs_lal
def test_entry_points_are_main_and_tasks(tmp_path):
    m = run(FIXTURE, tmp_path / "g.json")
    eps = {
        n.qualified_name: n.properties["entry_kind"]
        for n in m.nodes
        if n.properties.get("entry_point")
    }
    assert eps == {"Main": "main", "Counters.Worker": "task"}


@needs_lal
def test_more_than_5_percent_parse_failures_stops(tmp_path, capsys):
    repo = tmp_path / "bad"
    repo.mkdir()
    (repo / "a.adb").write_text("procedure A is begin null; end A;\n")
    (repo / "b.adb").write_text("procedure B is begin ((( end;\n")
    out = tmp_path / "g.json"
    assert main([str(repo), "--out", str(out), "--parser", "libadalang"]) == 1
    assert not out.exists()
    assert "more than 5%" in capsys.readouterr().err


@needs_ts
def test_treesitter_fallback_marks_everything_unresolved(tmp_path):
    m = run(FIXTURE, tmp_path / "g.json", "--parser", "treesitter")
    sem = [e for e in m.edges if e.type in ("CALLS", "MAY_CALL")]
    assert sem and all(e.properties["resolved"] is False for e in sem)
    assert all(ev.quality == "FALLBACK_PARSED" for ev in m.evidence)
    assert "Ada links are best-guess in this mode." in m.known_gaps
    assert not [e for e in m.edges if e.type in ("READS", "WRITES")]


@needs_lal
def test_broken_project_falls_back_and_keeps_main(tmp_path):
    repo = tmp_path / "r"
    (repo / "src").mkdir(parents=True)
    (repo / "p.gpr").write_text(
        'with "missing_deps.gpr";\nproject P is\n   for Main use ("m.adb");\nend P;\n'
    )
    (repo / "src" / "m.adb").write_text("procedure M is begin null; end M;\n")
    m = run(repo, tmp_path / "g.json", "--project", str(repo / "p.gpr"))
    assert any("could not be loaded" in g for g in m.known_gaps)
    assert [n.name for n in m.nodes if n.properties.get("entry_point")] == ["M"]


@needs_lal
def test_all_projects_merges_conflicting_variants(tmp_path):
    repo = tmp_path / "r"
    for v, body in (("a", "A_Impl"), ("b", "B_Impl")):
        (repo / v).mkdir(parents=True)
        (repo / v / "dev.ads").write_text("package Dev is\n   procedure Go;\nend Dev;\n")
        (repo / v / "dev.adb").write_text(
            f"package body Dev is\n   procedure Go is begin null; end Go;\n   procedure {body} is begin Go; end {body};\nend Dev;\n"
        )
        (repo / f"{v}.gpr").write_text(
            f'project {v.upper()} is\n   for Source_Dirs use ("{v}");\nend {v.upper()};\n'
        )
    m = run(repo, tmp_path / "g.json", "--all-projects")
    names = {n.qualified_name for n in m.nodes if n.kind == "Function"}
    assert {"Dev.A_Impl", "Dev.B_Impl"} <= names


@needs_lal
@pytest.mark.skipif(not ADL.is_dir(), reason="repos/ADL not cloned")
def test_adl_zero_parse_failures_and_semantics(tmp_path):
    m = run(ADL, tmp_path / "adl.json")
    assert not [c for c in m.coverage if c.status == "FAILED"]
    assert any(e.properties.get("via") in ("out", "in out") for _, _, e in edges(m, "WRITES"))
    sites: dict[tuple, int] = {}
    for s, _, e in edges(m, "MAY_CALL"):
        k = (s.id, e.properties["call_site_line"])
        sites[k] = sites.get(k, 0) + 1
    assert max(sites.values()) >= 2


@needs_ts
def test_cli_writes_the_shared_graph_format(tmp_path):
    out = tmp_path / "g.json"
    assert main([str(FIXTURE), "--out", str(out), "--parser", "treesitter"]) == 0
    g = load_graph(out)  # strict schema: unknown fields would fail here
    assert g.language == "ada"
    fns = [n for n in g.nodes if n.kind == "function"]
    assert fns and all(f.language == "ada" and f.line_end >= f.line_start for f in fns)
    ids = {n.id for n in g.nodes}
    assert all(e.source in ids and e.target in ids for e in g.edges)
    assert g.stats.nodes["function"] == len(fns)


@needs_ts
def test_converter_keeps_unresolved_calls_out_of_calls(tmp_path):
    from extractor.ada.convert import ncm_to_graph

    m = run(FIXTURE, tmp_path / "g.json", "--parser", "treesitter")
    g = ncm_to_graph(m.model_dump(mode="json"), "fixture")
    n_calls = sum(1 for e in m.edges if e.type == "CALLS")
    assert sum(e.count for e in g.edges if e.kind == "calls") <= n_calls
    assert not [e for e in g.edges if e.kind in ("reads", "writes")]  # tree-sitter finds no data access
