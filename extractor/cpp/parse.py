"""Steps C-E for one translation unit: parse with libclang, collect nodes and edges.

Everything here returns plain dicts (JSON-friendly) so results can be cached on disk and
sent back from worker processes.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from pathlib import Path

from clang import cindex
from clang.cindex import AccessSpecifier, Cursor, CursorKind as K, StorageClass, TokenKind, TypeKind

from .flags import CompileUnit, in_repo

sys.setrecursionlimit(30000)

FUNC_KINDS = {
    K.CXX_METHOD,
    K.FUNCTION_DECL,
    K.CONSTRUCTOR,
    K.DESTRUCTOR,
    K.FUNCTION_TEMPLATE,
    K.CONVERSION_FUNCTION,
}
CLASS_KINDS = {
    K.CLASS_DECL,
    K.STRUCT_DECL,
    K.UNION_DECL,
    K.CLASS_TEMPLATE,
    K.CLASS_TEMPLATE_PARTIAL_SPECIALIZATION,
}
SCOPE_KINDS = CLASS_KINDS | {K.NAMESPACE}
# Declaration kinds worth descending into while looking for definitions.
DESCEND_KINDS = SCOPE_KINDS | {K.LINKAGE_SPEC, K.UNEXPOSED_DECL, K.FRIEND_DECL}
ARRAY_TYPES = {TypeKind.CONSTANTARRAY, TypeKind.INCOMPLETEARRAY, TypeKind.VARIABLEARRAY}
INDIRECT_TYPES = {TypeKind.POINTER, TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE}
REF_TYPES = {TypeKind.LVALUEREFERENCE, TypeKind.RVALUEREFERENCE}
STD_FUNCTION_CLASSES = {"function", "move_only_function", "function_ref"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>="}
# `a = b` on a class type is a call to operator=: the first argument (a) is the thing that changes.
OVERLOADED_WRITE_OPS = {f"operator{op}" for op in ASSIGN_OPS | {"++", "--"}}

MAX_DIAGS_KEPT = 10


# ---------------------------------------------------------------- libclang extras (ctypes)

_ctypes_ready = False


def _setup_ctypes() -> None:
    global _ctypes_ready
    if _ctypes_ready:
        return
    lib = cindex.conf.lib
    lib.clang_getOverriddenCursors.argtypes = [
        Cursor,
        ctypes.POINTER(ctypes.POINTER(Cursor)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.clang_getOverriddenCursors.restype = None
    lib.clang_disposeOverriddenCursors.argtypes = [ctypes.POINTER(Cursor)]
    lib.clang_disposeOverriddenCursors.restype = None
    _ctypes_ready = True


def overridden_methods(cursor: Cursor) -> list[Cursor]:
    _setup_ctypes()
    arr = ctypes.POINTER(Cursor)()
    n = ctypes.c_uint(0)
    cindex.conf.lib.clang_getOverriddenCursors(cursor, ctypes.byref(arr), ctypes.byref(n))
    out = []
    try:
        for i in range(n.value):
            c = Cursor()
            ctypes.memmove(ctypes.byref(c), ctypes.byref(arr[i]), ctypes.sizeof(Cursor))
            c._tu = cursor._tu
            out.append(c)
    finally:
        if n.value:
            cindex.conf.lib.clang_disposeOverriddenCursors(arr)
    return out


def specialized_template(cursor: Cursor) -> Cursor | None:
    """For an instantiated function, the template it came from (None otherwise)."""
    try:
        t = cindex.conf.lib.clang_getSpecializedCursorTemplate(cursor)
    except Exception:
        return None
    if t is None or t.kind.is_invalid():
        return None
    return t


# ---------------------------------------------------------------- helpers


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def qualified_parts(cursor: Cursor) -> list[str]:
    parts: list[str] = []
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if p.kind in SCOPE_KINDS and p.spelling:
            parts.append(p.spelling)
        p = p.semantic_parent
    parts.reverse()
    return parts


def enclosing_class(cursor: Cursor) -> str | None:
    p = cursor.semantic_parent
    while p is not None and p.kind != K.TRANSLATION_UNIT:
        if p.kind in CLASS_KINDS and p.spelling:
            return p.spelling
        p = p.semantic_parent
    return None


def is_global_var(cursor: Cursor) -> bool:
    """VAR_DECL at namespace / class scope (not a local, static local or parameter)."""
    p = cursor.semantic_parent
    return p is not None and (p.kind in SCOPE_KINDS or p.kind == K.TRANSLATION_UNIT)


# ---------------------------------------------------------------- the per-TU collector


class Collector:
    def __init__(self, repo: Path, excludes: list[str]):
        self.repo = repo
        self.excludes = excludes
        self._rel_cache: dict[str, str | None] = {}
        self.functions: dict[str, dict] = {}
        self.fields: dict[str, dict] = {}
        self.class_bases: dict[str, list[str]] = {}
        self.class_docs: dict[str, str] = {}
        self.files: set[str] = set()

    # -- repo membership
    def rel(self, f) -> str | None:
        if f is None:
            return None
        name = f.name
        if name not in self._rel_cache:
            self._rel_cache[name] = in_repo(name, self.repo, self.excludes)
        return self._rel_cache[name]

    def cursor_rel(self, c: Cursor) -> str | None:
        return self.rel(c.location.file)

    # -- declaration walk
    def collect(self, tu_cursor: Cursor) -> None:
        for ch in tu_cursor.get_children():
            self.visit_decl(ch)

    def visit_decl(self, c: Cursor) -> None:
        rel = self.cursor_rel(c)
        if rel is None:
            return  # system header, other repo, or excluded: skip the whole subtree
        k = c.kind
        if k in FUNC_KINDS:
            if c.is_definition():
                self.record_function(c, rel)
            elif k == K.CXX_METHOD and c.is_pure_virtual_method():
                self.record_function(c, rel, abstract=True)  # a dispatch target with no body
        elif k == K.FIELD_DECL:
            self.record_field(c, rel)
        elif k == K.VAR_DECL:
            if is_global_var(c):
                self.record_field(c, rel)
        elif k in DESCEND_KINDS:
            if k in CLASS_KINDS and c.is_definition():
                self.record_class(c)
            for ch in c.get_children():
                self.visit_decl(ch)

    def record_field(self, c: Cursor, rel: str) -> None:
        usr = c.get_usr()
        if not usr or usr in self.fields or not c.spelling:
            return
        parts = qualified_parts(c)
        self.files.add(rel)
        self.fields[usr] = {
            "id": usr,
            "name": c.spelling,
            "qualified_name": "::".join(parts + [c.spelling]),
            "container": enclosing_class(c),
            "type": c.type.spelling,
            "file": rel,
            "line": c.location.line,
        }

    def record_class(self, c: Cursor) -> None:
        if not c.spelling:
            return
        bases = []
        for ch in c.get_children():
            if ch.kind != K.CXX_BASE_SPECIFIER:
                continue
            decl = ch.type.get_declaration()
            if decl.kind.is_invalid() or not decl.spelling:
                bases.append(ch.type.spelling)
            else:
                bases.append("::".join(qualified_parts(decl) + [decl.spelling]))
        name = "::".join(qualified_parts(c) + [c.spelling])
        known = self.class_bases.setdefault(name, [])
        known.extend(b for b in bases if b not in known)
        if c.raw_comment and name not in self.class_docs:
            self.class_docs[name] = c.raw_comment

    @staticmethod
    def is_public(c: Cursor) -> bool:
        if c.kind in (K.CXX_METHOD, K.CONSTRUCTOR, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
            return c.access_specifier == AccessSpecifier.PUBLIC
        if c.storage_class == StorageClass.STATIC:
            return False
        p = c.semantic_parent
        while p is not None and p.kind != K.TRANSLATION_UNIT:
            if p.kind == K.NAMESPACE and not p.spelling:
                return False  # anonymous namespace
            p = p.semantic_parent
        return True

    def record_function(self, c: Cursor, rel: str, abstract: bool = False) -> None:
        usr = c.get_usr()
        if not usr:
            return
        existing = self.functions.get(usr)
        if existing is not None and not (existing["node"]["is_pure_virtual"] and not abstract):
            return
        self.files.add(rel)
        name = c.spelling
        parts = qualified_parts(c)
        sig = c.displayname
        if c.kind not in (K.CONSTRUCTOR, K.DESTRUCTOR, K.CONVERSION_FUNCTION):
            sig = f"{c.result_type.spelling} {sig}"
        if c.kind == K.CXX_METHOD or c.kind == K.CONVERSION_FUNCTION:
            if c.is_const_method():
                sig += " const"
        start, end = c.extent.start.line, c.extent.end.line
        container = enclosing_class(c)
        is_virtual = bool(c.is_virtual_method()) if c.kind in (K.CXX_METHOD, K.DESTRUCTOR) else False
        rec = {
            "node": {
                "id": usr,
                "name": name,
                "qualified_name": "::".join(parts + [name]),
                "container": container,
                "language": "cpp",
                "file": rel,
                "line_start": start,
                "line_end": end,
                "signature": sig,
                "is_virtual": is_virtual,
                "is_pure_virtual": abstract,
                "is_public": self.is_public(c),
                "param_types": [a.type.get_canonical().spelling for a in c.get_arguments()],
                "is_entry_point": name == "main" and container is None and not parts,
                "loc": end - start + 1,
                "doc_comment": c.raw_comment,
            },
            "calls": {},  # (target_usr, kind) -> [lines]
            "accesses": {},  # (target_usr, "reads"/"writes") -> [lines]
            "overrides": [],
            "external_calls": 0,
            "unresolved_calls": 0,
            "indirect_calls": [],
            "recursive": False,
        }
        self.functions[usr] = rec
        if is_virtual:
            for base in overridden_methods(c):
                if self.cursor_rel(base) is not None and base.get_usr():
                    rec["overrides"].append(base.get_usr())
        if abstract:
            return
        stack: list[list] = [[c, 0]]
        for i, ch in enumerate(c.get_children()):
            stack[0][1] = i
            self.walk(ch, stack, rec)

    # -- function body walk (explicit parent stack: python bindings have no parent pointer)
    def walk(self, c: Cursor, stack: list[list], rec: dict) -> None:
        k = c.kind
        if k == K.CALL_EXPR:
            self.on_call(c, rec)
        elif k in (K.MEMBER_REF_EXPR, K.DECL_REF_EXPR):
            self.on_ref(c, stack, rec)
        elif k == K.MEMBER_REF and len(stack) == 1:
            # constructor initializer list: `: _parent(0)` writes the field
            self.on_ref(c, stack, rec, force_write=True)
        stack.append([c, 0])
        for i, ch in enumerate(c.get_children()):
            stack[-1][1] = i
            self.walk(ch, stack, rec)
        stack.pop()

    def canonical_function(self, ref: Cursor) -> Cursor:
        seen = 0
        while seen < 5:
            t = specialized_template(ref)
            if t is None:
                break
            ref = t
            seen += 1
        return ref

    def on_call(self, c: Cursor, rec: dict) -> None:
        ref = c.referenced
        if ref is None or ref.kind not in FUNC_KINDS or self.is_std_function_call(ref):
            # function pointer, std::function, callback, dependent call: target unknown
            rec["indirect_calls"].append(c.location.line)
            return
        ref = self.canonical_function(ref)
        if self.cursor_rel(ref) is None:
            rec["external_calls"] += 1
            return
        usr = ref.get_usr()
        if not usr:
            rec["unresolved_calls"] += 1
            return
        dispatch = ref.kind in (K.CXX_METHOD, K.DESTRUCTOR) and ref.is_virtual_method() and self.is_dispatching(c)
        if usr == rec["node"]["id"] and not dispatch:
            rec["recursive"] = True
            return
        # A dispatching call to its own method (base->Run() inside Base::Run) is kept: it can land on overrides.
        rec["calls"].setdefault((usr, "calls", dispatch), []).append(c.location.line)

    @staticmethod
    def is_std_function_call(ref: Cursor) -> bool:
        if ref.spelling != "operator()":
            return False
        p = ref.semantic_parent
        return p is not None and p.spelling in STD_FUNCTION_CLASSES

    def is_dispatching(self, call: Cursor) -> bool:
        """True when a virtual method is called through a pointer or reference (not `Base::f()`)."""
        callee = next(call.get_children(), None)
        if callee is None or callee.kind != K.MEMBER_REF_EXPR:
            return False
        toks = [t.spelling for t in callee.get_tokens()]
        if "::" in toks:
            return False  # qualified call: the compiler picked the exact function
        ops = [t for t in toks if t in (".", "->")]
        if not ops:
            return True  # implicit this->
        if ops[-1] == "->":
            return True
        base = next(callee.get_children(), None)
        return base is not None and self.is_indirect_object(base)

    def is_indirect_object(self, e: Cursor) -> bool:
        while e.kind in (K.UNEXPOSED_EXPR, K.PAREN_EXPR):
            e = next(e.get_children(), None)
            if e is None:
                return False
        if e.kind in (K.DECL_REF_EXPR, K.MEMBER_REF_EXPR):
            r = e.referenced
            return r is not None and r.type.get_canonical().kind in INDIRECT_TYPES
        if e.kind == K.CALL_EXPR:
            r = e.referenced
            return r is not None and r.result_type.get_canonical().kind in REF_TYPES
        if e.kind == K.CXX_THIS_EXPR:
            return True
        if e.kind == K.UNARY_OPERATOR:
            return next((t.spelling for t in e.get_tokens()), "") == "*"
        return False

    def on_ref(self, c: Cursor, stack: list[list], rec: dict, force_write: bool = False) -> None:
        ref = c.referenced
        if ref is None:
            return
        if ref.kind == K.FIELD_DECL:
            pass
        elif ref.kind == K.VAR_DECL and is_global_var(ref):
            pass
        else:
            return
        if self.cursor_rel(ref) is None:
            return
        usr = ref.get_usr()
        if not usr:
            return
        write = force_write or self.is_write(stack, ref)
        rec["accesses"].setdefault((usr, "writes" if write else "reads"), []).append(c.location.line)

    # -- read vs write
    def is_write(self, stack: list[list], ref: Cursor) -> bool:
        is_array = ref.type.get_canonical().kind in ARRAY_TYPES
        j = len(stack) - 1
        while j >= 0:
            pc, idx = stack[j]
            if pc.kind in (K.PAREN_EXPR, K.UNEXPOSED_EXPR):
                j -= 1
            elif pc.kind == K.ARRAY_SUBSCRIPT_EXPR and is_array and idx == 0:
                j -= 1  # arr[i] = v writes into the array field itself
            else:
                break
        if j < 0:
            return False
        pc, idx = stack[j]
        if pc.kind == K.CALL_EXPR:
            # libclang lists the object an operator acts on first (child 0), then the operator, then the other arguments
            ref = pc.referenced
            return idx == 0 and ref is not None and ref.spelling in OVERLOADED_WRITE_OPS
        if idx != 0:
            return False  # we are the right-hand side / an argument, not the target
        k = pc.kind
        if k == K.COMPOUND_ASSIGNMENT_OPERATOR:
            return True
        if k == K.BINARY_OPERATOR:
            return self.binary_op(pc) in ASSIGN_OPS
        if k == K.UNARY_OPERATOR:
            toks = [t.spelling for t in pc.get_tokens()]
            return bool(toks) and (toks[0] in ("++", "--") or toks[-1] in ("++", "--"))
        return False

    @staticmethod
    def binary_op(c: Cursor) -> str | None:
        lhs = next(c.get_children(), None)
        if lhs is None:
            return None
        end = lhs.extent.end.offset
        for t in c.get_tokens():
            if t.kind == TokenKind.PUNCTUATION and t.extent.start.offset >= end:
                return t.spelling
        return None


# ---------------------------------------------------------------- one translation unit

_index: cindex.Index | None = None


def _get_index() -> cindex.Index:
    global _index
    if _index is None:
        _index = cindex.Index.create()
    return _index


def parse_unit(unit: CompileUnit, repo: Path, excludes: list[str], extra_args: list[str]) -> dict:
    """Parse one source file and return a JSON-friendly result dict."""
    rel_src = in_repo(unit.file, repo, excludes) or unit.file
    result: dict = {
        "file": rel_src,
        "error_count": 0,
        "errors": [],
        "functions": [],
        "fields": [],
        "class_bases": {},
        "class_docs": {},
        "files": [],
        "deps": {},
    }
    old_cwd = os.getcwd()
    try:
        os.chdir(unit.directory if os.path.isdir(unit.directory) else str(repo))
        try:
            tu = _get_index().parse(unit.file, args=unit.args + extra_args)
        except cindex.TranslationUnitLoadError as e:
            result["error_count"] = 1
            result["errors"] = [f"{rel_src}: could not load translation unit ({e})"]
            return result
    finally:
        os.chdir(old_cwd)

    for d in tu.diagnostics:
        if d.severity >= cindex.Diagnostic.Error:
            result["error_count"] += 1
            if len(result["errors"]) < MAX_DIAGS_KEPT:
                loc = d.location
                where = loc.file.name if loc.file else rel_src
                result["errors"].append(f"{where}:{loc.line}: {d.spelling}")

    col = Collector(repo, excludes)
    col.rel_src = rel_src
    col.collect(tu.cursor)

    # content hashes of every repo file this TU depends on (used to validate the cache)
    deps = {unit.file: file_hash(unit.file)}
    for inc in tu.get_includes():
        name = inc.include.name
        if name not in deps and in_repo(name, repo, excludes) is not None:
            deps[name] = file_hash(name)
    result["deps"] = deps

    for usr, rec in col.functions.items():
        rec = dict(rec)
        rec["calls"] = [[t, k, lines, d] for (t, k, d), lines in rec["calls"].items()]
        rec["accesses"] = [[t, k, lines] for (t, k), lines in rec["accesses"].items()]
        result["functions"].append(rec)
    result["fields"] = list(col.fields.values())
    result["class_bases"] = col.class_bases
    result["class_docs"] = col.class_docs
    result["files"] = sorted(col.files | {rel_src} if in_repo(unit.file, repo, excludes) else col.files)
    return result
