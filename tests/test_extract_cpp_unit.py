"""Small, fast checks of the C++ extractor pieces on a hand-written repo."""

import json
import textwrap
from pathlib import Path

import pytest

from extractor.common.schema import FunctionNode
from extractor.cpp import flags
from extractor.cpp.build import ParseErrorLimit, extract_repo

HEADER = """
#pragma once
struct Base {
  virtual void f();
  int base_val = 0;
};
struct Derived : Base {
  void f() override;
  int x = 0;
  int arr[4];
  static int count;
  Derived() : x(1) {}
  inline int getX() const { return x; }
};
extern int g_total;
"""

SOURCE_A = """
#include "model.hpp"
#include <cstdlib>
int g_total = 0;
int Derived::count = 0;
void Base::f() {}
void Derived::f() { x = 1; }
void writer(Derived& d, int i) {
  d.x += 2;
  d.x++;
  --d.x;
  g_total = 3;
  d.arr[i] = 7;
  int y = d.x;        // read
  (void)y;
}
int fact(int n) { return n <= 1 ? 1 : n * fact(n - 1); }
void caller(Base& b, Derived& d, void (*cb)(int)) {
  b.f();            // virtual dispatch
  b.Base::f();      // direct
  d.getX();
  std::abs(-1);     // external
  cb(1);            // unresolved: function pointer
  writer(d, 0);
}
"""

SOURCE_B = """
#include "model.hpp"
int other(Derived& d) { return d.getX(); }
"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    repo = tmp_path_factory.mktemp("synthetic")
    (repo / "model.hpp").write_text(textwrap.dedent(HEADER))
    (repo / "a.cpp").write_text(textwrap.dedent(SOURCE_A))
    (repo / "b.cpp").write_text(textwrap.dedent(SOURCE_B))
    cache = tmp_path_factory.mktemp("cache")
    graph, report = extract_repo(repo, cache_dir=cache, jobs=1, log=lambda *_: None)
    return repo, cache, graph, report


def fn(graph, qname):
    (n,) = [x for x in graph.nodes if isinstance(x, FunctionNode) and x.qualified_name == qname]
    return n


def edges_from(graph, qname, kinds):
    nm = {n.id: getattr(n, "qualified_name", n.id) for n in graph.nodes}
    src = fn(graph, qname).id
    return sorted((e.kind, nm[e.target]) for e in graph.edges if e.source == src and e.kind in kinds)


def test_no_errors(built):
    assert built[3].error_count == 0


def test_header_function_is_deduplicated(built):
    graph = built[2]
    assert len([n for n in graph.nodes if getattr(n, "name", "") == "getX"]) == 1
    ids = [n.id for n in graph.nodes]
    assert len(ids) == len(set(ids))


def test_writes_and_reads(built):
    graph = built[2]
    got = edges_from(graph, "writer", {"reads", "writes"})
    assert ("writes", "Derived::x") in got  # +=, ++, --
    assert ("writes", "g_total") in got
    assert ("writes", "Derived::arr") in got  # arr[i] = v
    assert ("reads", "Derived::x") in got  # int y = d.x
    assert ("reads", "Derived::arr") not in got


def test_constructor_initializer_is_a_write(built):
    assert ("writes", "Derived::x") in edges_from(built[2], "Derived::Derived", {"writes"})


def call_edges(graph, qname):
    nm = {n.id: getattr(n, "qualified_name", n.id) for n in graph.nodes}
    src = fn(graph, qname).id
    return sorted((e.kind, nm[e.target], e.dispatch) for e in graph.edges if e.source == src and e.kind in ("calls", "may_call"))


def test_virtual_vs_direct_calls(built):
    got = call_edges(built[2], "caller")
    assert ("calls", "Base::f", True) in got  # b.f(): dispatches through a reference
    assert ("calls", "Base::f", False) in got  # b.Base::f(): direct
    assert ("calls", "Derived::getX", False) in got
    assert ("calls", "writer", False) in got
    assert not any(k == "may_call" for k, _, _ in got)  # the extractor never invents these


def test_qualified_virtual_call_is_direct(tmp_path):
    (tmp_path / "x.cpp").write_text("struct B{virtual void f(){}}; void g(B& b){ b.B::f(); }")
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    assert call_edges(graph, "g") == [("calls", "B::f", False)]


def test_virtual_call_on_object_by_value_is_not_dispatch(tmp_path):
    (tmp_path / "x.cpp").write_text(
        "struct B{virtual void f(){}}; struct H{B b; B* p;}; void g(H& h){ h.b.f(); h.p->f(); }"
    )
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    assert call_edges(graph, "g") == [("calls", "B::f", False), ("calls", "B::f", True)]


def test_overrides_edge(built):
    assert ("overrides", "Base::f") in edges_from(built[2], "Derived::f", {"overrides"})


def test_recursion_flag_and_no_self_edge(built):
    graph = built[2]
    assert fn(graph, "fact").recursive is True
    assert edges_from(graph, "fact", {"calls", "may_call"}) == []


def test_external_and_unresolved_counts(built):
    n = fn(built[2], "caller")
    assert n.external_calls >= 1  # std::abs
    assert len(n.indirect_calls) == 1  # call through a function pointer: line recorded, no target


def test_defined_in_edges_and_file_nodes(built):
    graph = built[2]
    paths = {n.path for n in graph.nodes if n.kind == "file"}
    assert {"a.cpp", "model.hpp"} <= paths
    assert graph.stats.edges["defined_in"] == graph.stats.nodes["function"] + graph.stats.nodes["field"]


def test_class_bases_recorded(built):
    assert built[2].class_bases["Derived"] == ["Base"]
    assert built[2].class_bases["Base"] == []


def test_pure_virtual_declaration_becomes_a_node(tmp_path):
    (tmp_path / "x.cpp").write_text(
        "struct I{virtual int f() = 0;}; struct C : I { int f() override { return 1; } };"
        "int use(I& i){ return i.f(); }"
    )
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    assert fn(graph, "I::f").is_pure_virtual is True
    assert ("overrides", "I::f") in edges_from(graph, "C::f", {"overrides"})
    assert ("calls", "I::f") in edges_from(graph, "use", {"calls"})


def test_std_function_call_is_indirect(tmp_path):
    (tmp_path / "x.cpp").write_text(
        "#include <functional>\nvoid run(std::function<void(int)> cb){ cb(1); }"
    )
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    assert len(fn(graph, "run").indirect_calls) == 1


def test_function_metadata(built):
    n = fn(built[2], "fact")
    assert n.file == "a.cpp" and n.loc == 1 and n.container is None and n.language == "cpp"
    assert fn(built[2], "Derived::f").container == "Derived"
    assert fn(built[2], "Derived::f").is_virtual is True


def test_cache_reuse_and_invalidation(built, tmp_path_factory):
    repo, cache, _, _ = built
    _, r2 = extract_repo(repo, cache_dir=cache, jobs=1, log=lambda *_: None)
    assert r2.cache_hits == r2.files_total == 2
    # touching a header invalidates every file that includes it
    (repo / "model.hpp").write_text((repo / "model.hpp").read_text() + "\n// changed\n")
    _, r3 = extract_repo(repo, cache_dir=cache, jobs=1, log=lambda *_: None)
    assert r3.cache_hits == 0


def test_too_many_parse_errors_refuses_to_build(tmp_path):
    (tmp_path / "bad.cpp").write_text("int f( { this is not c++ ;")
    with pytest.raises(ParseErrorLimit):
        extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)


# ---- flag helpers


def test_clean_args():
    args = ["/usr/bin/c++", "-I/inc", "-DX=1", "-o", "x.o", "-c", "/r/a.cpp", "-MD", "-MF", "x.d", "-std=c++17"]
    assert flags.clean_args(args, "/r/a.cpp", "/r") == ["-I/inc", "-DX=1", "-std=c++17"]


def test_clean_args_relative_source():
    assert flags.clean_args(["cc", "-c", "src/a.cpp", "-O2"], "src/a.cpp", "/r") == ["-O2"]


def test_parse_search_list():
    text = textwrap.dedent(
        """\
        ignoring nonexistent directory "/nope"
        #include "..." search starts here:
        #include <...> search starts here:
         /usr/include/c++/v1
         /usr/include
         /System/Library/Frameworks (framework directory)
        End of search list.
         /not/included
        """
    )
    assert flags.parse_search_list(text) == [
        "-isystem/usr/include/c++/v1",
        "-isystem/usr/include",
        "-iframework/System/Library/Frameworks",
    ]


def test_exclude_patterns():
    ex = flags.DEFAULT_EXCLUDES + ["*_test.cpp"]
    assert flags.is_excluded("third_party/x/a.cpp", ex)
    assert flags.is_excluded("src/vendor/a.cpp", ex)
    assert flags.is_excluded("src/foo_test.cpp", ex)
    assert not flags.is_excluded("src/a.cpp", ex)
    assert not flags.is_excluded("src/rebuild/a.cpp", ex)


def test_compile_database_is_used(tmp_path):
    (tmp_path / "a.cpp").write_text("int a() { return 1; }")
    build = tmp_path / "build"
    build.mkdir()
    (build / "compile_commands.json").write_text(
        json.dumps(
            [{"directory": str(tmp_path), "command": "c++ -DFOO -c a.cpp -o a.o", "file": "a.cpp"}]
        )
    )
    units, source = flags.find_compile_units(tmp_path.resolve(), None, flags.DEFAULT_EXCLUDES)
    assert "compile_commands.json" in source
    assert len(units) == 1
    # libclang's database may add --driver-mode; what matters is -c / -o / source are gone
    assert "-DFOO" in units[0].args
    assert not {"-c", "-o", "a.o", "a.cpp"} & set(units[0].args)


def test_assignment_through_an_overloaded_operator_is_a_write(tmp_path):
    (tmp_path / "x.cpp").write_text(
        textwrap.dedent(
            """
            struct Vec { double d[3]; Vec& operator=(const Vec& o) { d[0] = o.d[0]; return *this; }
                         Vec& operator+=(const Vec& o) { d[0] += o.d[0]; return *this; } };
            struct State { Vec pos; Vec vel; int n; Vec get() const { return pos; } };
            struct Holder {
              State s; Vec other;
              void set(const Vec& v) { s.pos = v; }              // class-typed assignment: operator=
              void add(const Vec& v) { s.vel += v; n_ = 1; }     // operator+=
              Vec peek() const { Vec copy = other; return copy; } // only reads
              int n_ = 0;
            };
            """
        )
    )
    graph, _ = extract_repo(tmp_path, cache_dir=None, jobs=1, log=lambda *_: None)
    got = edges_from(graph, "Holder::set", {"reads", "writes"})
    assert ("writes", "State::pos") in got
    assert ("reads", "State::pos") not in got
    got = edges_from(graph, "Holder::add", {"reads", "writes"})
    assert ("writes", "State::vel") in got and ("writes", "Holder::n_") in got
    got = edges_from(graph, "Holder::peek", {"reads", "writes"})
    assert ("reads", "Holder::other") in got and not [g for g in got if g[0] == "writes"]
