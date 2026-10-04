"""Primary Ada backend: libadalang (semantic name resolution).

Mapping to the NormalizedCodeModel:
  subprogram / entry / expression-function / task bodies -> Function nodes (id from the body)
  package vars, record components, protected components   -> Variable nodes
  resolved calls -> CALLS; dispatching calls -> CALLS (declared primitive) + MAY_CALL (overrides)
  access-to-subprogram calls -> UNRESOLVED_CALL to an ExternalSymbol, counted as indirect_calls
  ":=" targets and actuals of "out"/"in out" parameters -> WRITES; other references -> READS
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from .builder import ModelBuilder

_LAL_HOME = os.environ.get("LIBADALANG_PYTHONPATH", os.path.expanduser("~/tools/lal/pylib"))
if os.path.isdir(_LAL_HOME) and _LAL_HOME not in sys.path:
    sys.path.append(_LAL_HOME)

try:
    import libadalang as lal

    _OK = True
except Exception:  # noqa: BLE001  # ImportError, or a shared-library load failure
    lal = None  # type: ignore[assignment]
    _OK = False


class ProjectLoadError(Exception):
    """The .gpr could not be loaded (e.g. a generated dependency project is missing)."""


def available() -> bool:
    return _OK


def version() -> str:
    return getattr(lal, "__version__", "unknown") if _OK else "n/a"


# --- helpers ---------------------------------------------------------------------------------


def _span(node) -> tuple[tuple[int, int], tuple[int, int]]:
    r = node.sloc_range
    return (r.start.line, r.start.column), (r.end.line, r.end.column)


def _sig(node) -> str:
    spec = node.p_subp_spec_or_null()
    text = spec.text if spec is not None else node.text.splitlines()[0]
    return re.sub(r"\s+", " ", text).strip()


def _fqn(node) -> str:
    """Fully qualified name; falls back to the plain name when libadalang cannot compute one."""
    try:
        return node.p_fully_qualified_name
    except lal.PropertyError:
        try:
            return node.p_defining_name.text
        except lal.PropertyError:
            return node.text.split()[0] if node.text else "?"


PROP_FAILURES: set[tuple[str, int, int]] = set()


def _prop(node, name: str, default=False):
    """Evaluate a libadalang property; name-resolution crashes are recorded and give `default`."""
    try:
        v = getattr(node, name)
        return v() if callable(v) else v
    except lal.PropertyError:
        r = node.sloc_range.start
        PROP_FAILURES.add((node.unit.filename, r.line, r.column))
        return default


def _is_body(node) -> bool:
    return isinstance(node, (lal.SubpBody, lal.ExprFunction, lal.EntryBody, lal.TaskBody))


def _func_like(node) -> bool:
    if isinstance(node, lal.EnumLiteralDecl):
        return False
    return isinstance(
        node,
        (
            lal.SubpBody,
            lal.ExprFunction,
            lal.EntryBody,
            lal.TaskBody,
            lal.BasicSubpDecl,
            lal.EntryDecl,
            lal.NullSubpDecl,
        ),
    )


class _Extractor:
    def __init__(self, builder: ModelBuilder, units: list, all_units: list) -> None:
        self.b = builder
        self.units = units
        self.all_units = all_units
        self.func_ids: dict[tuple, str] = {}
        self.var_ids: dict[tuple, str] = {}
        self.module_ids: dict[str, str] = {}
        self.override_cache: dict[tuple, list] = {}
        self.unresolved = 0

    # --- location helpers ---
    def in_repo(self, node) -> bool:
        try:
            return Path(node.unit.filename).resolve().is_relative_to(self.b.repo)
        except (OSError, ValueError):
            return False

    @staticmethod
    def key(node) -> tuple:
        r = node.sloc_range
        return (node.unit.filename, r.start.line, r.start.column)

    # --- containers ---
    def container_of(self, node):
        """Nearest enclosing package / protected / task entity, else enclosing subprogram."""
        p = node.parent
        while p is not None:
            if isinstance(
                p,
                (
                    lal.PackageBody,
                    lal.PackageDecl,
                    lal.ProtectedBody,
                    lal.ProtectedTypeDecl,
                    lal.SingleProtectedDecl,
                    lal.TaskBody,
                    lal.TaskTypeDecl,
                    lal.SingleTaskDecl,
                    lal.GenericPackageDecl,
                ),
            ):
                return p
            if isinstance(p, (lal.SubpBody, lal.ExprFunction)) and p is not node:
                return p
            p = p.parent
        return None

    def module_node(self, c) -> str | None:
        if c is None:
            return None
        if isinstance(c, lal.GenericPackageDecl):
            c = c.f_package_decl
        name = _fqn(c)
        if name in self.module_ids:
            return self.module_ids[name]
        kind = type(c).__name__
        mid = self.b.add_node(
            "Module",
            name.split(".")[-1],
            name,
            c.unit.filename,
            _span(c),
            properties={"ada_kind": kind},
        )
        self.module_ids[name] = mid
        return mid

    # --- functions ---
    def func_node(self, decl) -> str | None:
        """Function node for a subprogram decl: placed on its body when one exists."""
        if decl is None:
            return None
        target = decl
        if not _is_body(decl):
            try:
                body = decl.p_body_part_for_decl()
            except lal.PropertyError:
                body = None
            if body is not None:
                target = body
        k = self.key(target)
        if k in self.func_ids:
            return self.func_ids[k]
        if not self.in_repo(target):
            return None
        is_task = isinstance(target, lal.TaskBody)
        props: dict[str, Any] = {"has_body": _is_body(target)}
        if isinstance(target, lal.AbstractSubpDecl):
            props["abstract"] = True
        if isinstance(target, lal.EntryBody):
            props["ada_kind"] = "entry"
        if is_task:
            props.update(ada_kind="task_body", entry_point=True, entry_kind="task")
        cont = self.container_of(target)
        props["container"] = _fqn(cont) if cont is not None else None
        fq = _fqn(target)
        fid = self.b.add_node(
            "Function",
            fq.split(".")[-1],
            fq,
            target.unit.filename,
            _span(target),
            signature=("task body" if is_task else _sig(target)),
            properties=props,
        )
        self.func_ids[k] = fid
        fnode = self.b.file_node(target.unit.filename)
        self.b.add_edge(fnode, fid, "DEFINES", self.b.nodes[fid].evidence_ids[0])
        mid = self.module_node(cont)
        if mid:
            self.b.add_edge(mid, fid, "CONTAINS", self.b.nodes[fid].evidence_ids[0])
        return fid

    def overrides(self, decl) -> list:
        k = self.key(decl)
        if k not in self.override_cache:
            try:
                self.override_cache[k] = list(decl.p_find_all_overrides(self.all_units))
            except lal.PropertyError:
                self.override_cache[k] = []
        return self.override_cache[k]

    # --- variables ---
    def collect_variables(self, unit) -> None:
        for node in unit.root.finditer((lal.ObjectDecl, lal.ComponentDecl)):
            if isinstance(node, lal.ObjectDecl):
                cont = node.parent
                while cont is not None and not isinstance(
                    cont,
                    (
                        lal.PackageDecl,
                        lal.PackageBody,
                        lal.SubpBody,
                        lal.ExprFunction,
                        lal.EntryBody,
                        lal.TaskBody,
                        lal.ProtectedBody,
                        lal.BlockStmt,
                    ),
                ):
                    cont = cont.parent
                if not isinstance(cont, (lal.PackageDecl, lal.PackageBody)):
                    continue  # only package-level objects; locals are not graph nodes
                fk = "package_variable"
            else:
                fk = "record_component"
                p = node.parent
                while p is not None and not isinstance(
                    p, (lal.ProtectedDef, lal.RecordTypeDef, lal.TaskDef)
                ):
                    p = p.parent
                if isinstance(p, lal.ProtectedDef):
                    fk = "protected_component"
                elif p is None or isinstance(p, lal.TaskDef):
                    continue
            for dn in node.p_defining_names:
                fq = _fqn(dn)
                props = {"field_kind": fk}
                if isinstance(node, lal.ObjectDecl) and node.f_has_constant.p_as_bool:
                    props["constant"] = True
                vid = self.b.add_node(
                    "Variable",
                    dn.text,
                    fq,
                    unit.filename,
                    _span(node),
                    signature=re.sub(r"\s+", " ", node.text.split(":=")[0]).strip()[:200],
                    properties=props,
                )
                self.var_ids[
                    (unit.filename, dn.sloc_range.start.line, dn.sloc_range.start.column)
                ] = vid
                cont2 = self.container_of(node)
                mid = self.module_node(cont2)
                if mid:
                    self.b.add_edge(mid, vid, "CONTAINS", self.b.nodes[vid].evidence_ids[0])

    def var_for_decl(self, decl, name_text: str) -> str | None:
        if decl is None or not isinstance(decl, (lal.ObjectDecl, lal.ComponentDecl)):
            return None
        for dn in decl.p_defining_names:
            if dn.text.lower() == name_text.lower():
                return self.var_ids.get(
                    (decl.unit.filename, dn.sloc_range.start.line, dn.sloc_range.start.column)
                )
        return None

    # --- body analysis ---
    def enclosing_func(self, node):
        p = node.parent
        while p is not None:
            if _is_body(p):
                return p
            p = p.parent
        return None

    def chain_names(self, expr) -> list:
        """Leaf identifiers that denote the object whose value is written through `expr`."""
        out = []
        while expr is not None:
            if isinstance(expr, lal.Identifier):
                out.append(expr)
                break
            if isinstance(expr, lal.DottedName):
                out.append(expr.f_suffix)
                expr = expr.f_prefix
            elif isinstance(expr, lal.CallExpr):
                expr = expr.f_name
            elif isinstance(expr, lal.ExplicitDeref):
                expr = expr.f_prefix
            elif isinstance(expr, lal.ParenExpr):
                expr = expr.f_expr
            else:
                break
        return out

    def run_unit(self, unit) -> None:
        # Writes first, so their identifiers are not also reported as reads.
        consumed: set[tuple] = set()
        writes: list[tuple[Any, Any, str, dict]] = []  # (function body, expr, ev-node, props)

        for stmt in unit.root.finditer(lal.AssignStmt):
            fn = self.enclosing_func(stmt)
            if fn is not None:
                writes.append((fn, stmt.f_dest, stmt, {"via": ":="}))
        for call in unit.root.finditer(lal.Name):
            if (
                not isinstance(call, lal.Identifier)
                or not _prop(call, "p_is_call")
                or _prop(call, "p_is_defining")
            ):
                continue
            fn = self.enclosing_func(call)
            if fn is None:
                continue
            try:
                params = list(call.p_call_params)
            except lal.PropertyError:
                continue
            for pa in params:
                prm = pa.param
                if prm is None:
                    continue
                spec = prm.parent.parent if prm.parent is not None else None
                mode = getattr(spec, "f_mode", None)
                if isinstance(mode, (lal.ModeOut, lal.ModeInOut)):
                    writes.append(
                        (
                            fn,
                            pa.actual,
                            pa.actual,
                            {
                                "via": "out" if isinstance(mode, lal.ModeOut) else "in out",
                                "param": prm.text,
                                "call_site_line": call.sloc_range.start.line,
                            },
                        )
                    )

        for fn, expr, ev_node, props in writes:
            fid = self.func_ids.get(self.key(fn)) or self.func_node(fn)
            if fid is None:
                continue
            chain = self.chain_names(expr)
            consumed.update(self.key(c) for c in chain)
            for ident in chain:
                try:
                    decl = ident.p_referenced_decl()
                except lal.PropertyError:
                    continue
                vid = self.var_for_decl(decl, ident.text)
                if vid:
                    ev = self.b.add_evidence(unit.filename, *_span(ev_node))
                    self.b.add_edge(
                        fid, vid, "WRITES", ev, props, line=ev_node.sloc_range.start.line
                    )

        for ident in unit.root.finditer(lal.Identifier):
            fn = self.enclosing_func(ident)
            if fn is None or _prop(ident, "p_is_defining") or isinstance(ident.parent, lal.EndName):
                continue
            fid = self.func_ids.get(self.key(fn)) or self.func_node(fn)
            if fid is None:
                continue
            if _prop(ident, "p_is_call"):
                self.handle_call(unit, fid, fn, ident)
            elif self.key(ident) not in consumed:
                try:
                    decl = ident.p_referenced_decl()
                except lal.PropertyError:
                    continue
                vid = self.var_for_decl(decl, ident.text)
                if vid:
                    ev = self.b.add_evidence(unit.filename, *_span(ident))
                    self.b.add_edge(fid, vid, "READS", ev, line=ident.sloc_range.start.line)

        for deref in unit.root.finditer(lal.ExplicitDeref):
            if _prop(deref, "p_is_call") and _prop(deref, "p_is_access_call"):
                fn = self.enclosing_func(deref)
                fid = self.func_node(fn) if fn is not None else None
                if fid:
                    self.indirect(unit, fid, deref)

    def indirect(self, unit, fid: str, name) -> None:
        line = name.sloc_range.start.line
        ev = self.b.add_evidence(unit.filename, *_span(name))
        ext = self.b.external(
            name.text, unit.filename, line, "call through access-to-subprogram value", ev
        )
        if self.b.add_edge(fid, ext, "UNRESOLVED_CALL", ev, {"call_site_line": line}, line=line):
            self.b.indirect_calls += 1

    def handle_call(self, unit, fid: str, fn, ident) -> None:
        line = ident.sloc_range.start.line
        # whole call expression for evidence (e.g. "Draw (S)")
        expr = ident
        while True:
            par = expr.parent
            if (
                isinstance(par, lal.CallExpr)
                and par.f_name == expr
                or isinstance(par, lal.DottedName)
                and par.f_suffix == expr
            ):
                expr = par
            else:
                break
        ev = self.b.add_evidence(unit.filename, *_span(expr))
        if _prop(ident, "p_is_access_call"):
            self.indirect(unit, fid, ident)
            return
        try:
            decl = ident.p_referenced_decl()
        except lal.PropertyError:
            decl = None
        if decl is None:
            self.unresolved += 1
            ext = self.b.external(ident.text, unit.filename, line, "name resolution failed", ev)
            self.b.add_edge(fid, ext, "UNRESOLVED_CALL", ev, {"call_site_line": line}, line=line)
            return
        if not _func_like(decl):
            return  # type conversion, enum literal, etc.
        tid = self.func_node(decl)
        if tid is None:
            # resolved, but defined outside the analysed repo (e.g. the runtime)
            ext = self.b.external(
                _fqn(decl), unit.filename, line, "defined outside the analysed repo", ev
            )
            self.b.add_edge(
                fid, ext, "CALLS", ev, {"call_site_line": line, "external": True}, line=line
            )
            return
        dispatch = False
        try:
            dispatch = bool(_prop(ident, "p_is_dispatching_call"))
        except lal.PropertyError:
            pass
        props = {"call_site_line": line, "dispatching": dispatch}
        self.b.add_edge(fid, tid, "CALLS", ev, props, line=line)
        if dispatch:
            cands = []
            for ov in self.overrides(decl):
                oid = self.func_node(ov)
                if oid and oid != tid:
                    cands.append(oid)
            for oid in cands:
                self.b.add_edge(
                    fid,
                    oid,
                    "MAY_CALL",
                    ev,
                    {"call_site_line": line, "via": "dispatch", "candidates": cands},
                    line=line,
                )


def _runtime_sources() -> list[str]:
    """GNAT runtime .ads/.adb files (from an Alire or system toolchain), so `Ada.*` resolves."""
    import glob

    pats = [
        os.path.expanduser("~/.local/share/alire/toolchains/gnat_native*/lib/gcc/*/*/adainclude"),
        os.path.expanduser(
            "~/.config/alire/cache/dependencies/gnat_native*/lib/gcc/*/*/adainclude"
        ),
    ]
    for pat in pats:
        for d in sorted(glob.glob(pat)):
            return sorted(glob.glob(d + "/*.ad[sb]"))
    return []


def _gpr_mains(gpr: Path) -> list[str]:
    m = re.search(r"for\s+Main\s+use\s*\(([^)]*)\)", gpr.read_text(errors="replace"), re.IGNORECASE)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def project_members(gpr: Path) -> list[Path]:
    """Source files of a .gpr tree, read in a throwaway process.

    libadalang keeps global error state after a failed project load, which breaks every later
    context in the same process, so loading is always isolated. Raises ProjectLoadError.
    """
    code = (
        "import json,sys;from extractor.ada import lal;"
        "p=lal.lal.GPRProject(sys.argv[1],print_errors=False);"
        "print(json.dumps(list(p.source_files(lal.lal.SourceFilesMode.whole_project))))"
    )
    r = subprocess.run(
        [sys.executable, "-c", code, str(gpr)], capture_output=True, text=True, check=False
    )
    if r.returncode != 0:
        lines = [x for x in (r.stderr + r.stdout).strip().splitlines() if x.strip()]
        reason = next(
            (x for x in lines if "error:" in x and "ProjectError" not in x),
            lines[-1] if lines else "?",
        )
        raise ProjectLoadError(reason)
    return [Path(f).resolve() for f in json.loads(r.stdout.strip().splitlines()[-1])]


def select_variants(
    repo: Path, gprs: list[Path], max_variants: int = 10
) -> tuple[list[tuple[Path, list[Path], list[Path]]], list[str]]:
    """Greedy set cover: the fewest projects whose sources together cover the repo.

    One project is one coherent variant (a board, a demo...); two projects that define the same
    unit name differently cannot share a libadalang context, so each is analysed on its own.
    Returns ([(gpr, repo_files, all_files)], notes about projects that could not be loaded).
    """
    cands: list[tuple[Path, list[Path], list[Path]]] = []
    notes: list[str] = []
    for i, g in enumerate(gprs, 1):
        print(
            f"[probe {i}/{len(gprs)}] {g.relative_to(repo).as_posix()}", file=sys.stderr, flush=True
        )
        try:
            allm = project_members(g)
            mem = [m for m in allm if m.is_relative_to(repo)]
        except ProjectLoadError as exc:
            notes.append(f"{g.relative_to(repo).as_posix()}: not loaded ({exc})")
            continue
        if mem:
            cands.append((g, mem, allm))
    covered: set[Path] = set()
    chosen: list[tuple[Path, list[Path], list[Path]]] = []
    while len(chosen) < max_variants:
        best = max(cands, key=lambda c: len(set(c[1]) - covered), default=None)
        if best is None or not set(best[1]) - covered:
            break
        chosen.append(best)
        covered |= set(best[1])
        cands.remove(best)
    return chosen, notes


def _analyse(
    builder: ModelBuilder,
    files: list[Path],
    provider_files: list[str],
    mains_from: Path | None,
) -> None:
    """Analyse one coherent set of units in its own libadalang context and merge into builder."""
    ctx = lal.AnalysisContext(
        unit_provider=lal.UnitProvider.auto([*provider_files, *_runtime_sources()]),
        with_trivia=False,
    )
    units = []
    for f in files:
        u = ctx.get_from_file(str(f))
        units.append(u)
        if u.diagnostics:
            d = u.diagnostics[0]
            builder.set_coverage(f, "FAILED", f"{d.sloc_range.start.line}: {d.message}")
        elif builder.coverage.get(builder.rel(f)) is None:
            builder.set_coverage(f, "PARSED")
    good = [u for u in units if not u.diagnostics]

    ex = _Extractor(builder, good, good)
    for u in good:
        builder.file_node(u.filename)
        ex.collect_variables(u)
    for u in good:
        for node in u.root.finditer((lal.SubpBody, lal.ExprFunction, lal.EntryBody, lal.TaskBody)):
            ex.func_node(node)
    for u in good:
        ex.run_unit(u)

    if mains_from is not None:
        mains = {Path(m).stem.lower() for m in _gpr_mains(mains_from)}
        for u in good:
            if Path(u.filename).stem.lower() in mains and u.filename.endswith(".adb"):
                for body in u.root.finditer(lal.SubpBody):
                    if isinstance(body.parent, lal.LibraryItem):
                        fid = ex.func_node(body)
                        if fid:
                            builder.nodes[fid].properties.update(
                                entry_point=True, entry_kind="main"
                            )
    builder.counters["unresolved_calls"] += ex.unresolved


def extract(
    builder: ModelBuilder,
    repo: Path,
    files: list[Path],
    projects: list[Path],
    all_projects: bool = False,
    max_variants: int = 10,
) -> tuple[int, int, str]:
    """Run libadalang over the repo. Returns (failed_units, total_units, label).

    No project -> one auto-provider context over every file. With projects (or --all-projects,
    which discovers every .gpr) each selected project is analysed as its own variant and the
    results are merged; files no project covers get an auto-provider context of their own.
    """
    variants: list[tuple[Path | None, list[Path], list[Path]]] = []
    failed_gprs: list[Path] = []
    if all_projects:
        projects = sorted(repo.rglob("*.gpr"))
        chosen, notes = select_variants(repo, projects, max_variants)
        builder.known_gaps.extend(f"project {n}" for n in notes)
        variants = chosen
    else:
        for g in projects:
            try:
                allm = project_members(g)
                variants.append((g, [m for m in allm if m.is_relative_to(repo)], allm))
            except ProjectLoadError as exc:
                failed_gprs.append(g)
                builder.known_gaps.append(
                    f"project {g.name} could not be loaded ({exc}); its sources fall back to the auto provider"
                )
    covered = {f for _, m, _ in variants for f in m}
    rest = [f for f in files if f.resolve() not in covered]
    if rest and all_projects:
        builder.known_gaps.append(
            f"{len(rest)} file(s) not covered by the {len(variants)} selected variants were analysed "
            "in a shared auto context; names that clash with a variant may stay unresolved"
        )
    if rest or not variants:
        variants.append((None, rest or files, rest or files))

    for i, (gpr, members, provider) in enumerate(variants, 1):
        print(
            f"[variant {i}/{len(variants)}] {gpr.name if gpr else 'auto'}: {len(members)} files",
            file=sys.stderr,
            flush=True,
        )
        _analyse(
            builder,
            members,
            [str(f) for f in provider],
            gpr or (failed_gprs[0] if failed_gprs else None),
        )

    nvar = len(variants)
    label = (
        "libadalang/auto"
        if nvar == 1 and variants[0][0] is None
        else f"libadalang/{nvar} variant(s)"
    )
    c = builder.counters
    c["unevaluable_names"] = len(PROP_FAILURES)
    if c["unresolved_calls"]:
        builder.known_gaps.append(
            f"{c['unresolved_calls']} call(s) could not be resolved (missing units outside the repo?)"
        )
    if c["unevaluable_names"]:
        builder.known_gaps.append(
            f"libadalang could not evaluate {c['unevaluable_names']} name(s) (null-access in name resolution)"
        )
    if builder.indirect_calls:
        builder.known_gaps.append(
            f"{builder.indirect_calls} call(s) through access-to-subprogram values are not resolved"
        )
    failed = sum(1 for c_ in builder.coverage.values() if c_.status == "FAILED")
    return failed, len(builder.coverage), label
