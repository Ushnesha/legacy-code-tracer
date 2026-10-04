"""FastAPI server over the SQLite stores in data/<name>.db.

All node ids in URLs and responses are the short ids from server.store.
"""

from __future__ import annotations

import functools
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterator, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from extractor.common.flags import function_flags
from extractor.common import explain as X  # the impact rules, shared with the explain step
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import models as M
from .store import DATA_DIR, connect, migrate, resolve_repo_dir

REPO_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_CHILDREN = 40
MAX_TREE_NODES = 3000  # safety budget for one tree response
CALL_KINDS = ("calls", "may_call")

# Summary comes from the summaries table when the explain step has filled it in.
FN_COLS = """f.id, f.name, f.qualified_name, f.container, f.file, f.line_start, f.line_end,
    f.is_virtual, f.is_pure_virtual, f.is_entry_point, f.is_noise, f.noise_reason, f.loc,
    f.fan_in, f.fan_out, f.reach, COALESCE(s.short, f.summary) AS summary,
    s.model AS summary_model, s.verified AS summary_verified, f.writes_count, f.indirect_count"""
SUM_JOIN = "LEFT JOIN summaries s ON s.function_id = f.id"
FN_FROM = f"functions f {SUM_JOIN}"


def offline_mode() -> bool:
    """AEROTRACE_OFFLINE=1: serve only what is saved on disk; never call the AI."""
    return os.environ.get("AEROTRACE_OFFLINE", "").strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- helpers


def brief(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "qualified_name": row["qualified_name"],
        "container": row["container"],
        "file": row["file"],
        "line_start": row["line_start"],
        "line_end": row["line_end"],
        "is_virtual": bool(row["is_virtual"]),
        "is_pure_virtual": bool(row["is_pure_virtual"]),
        "is_entry_point": bool(row["is_entry_point"]),
        "is_noise": bool(row["is_noise"]),
        "noise_reason": row["noise_reason"],
        "loc": row["loc"],
        "fan_in": row["fan_in"],
        "fan_out": row["fan_out"],
        "reach": row["reach"],
        "summary": row["summary"],
        "summary_model": row["summary_model"],
        "summary_verified": None if row["summary_verified"] is None else bool(row["summary_verified"]),
        "fields_written_count": row["writes_count"],
        "indirect_calls": row["indirect_count"],
    }


def fn_order(d: dict) -> tuple:
    """Non-noise first, then biggest reach, then a stable tiebreak."""
    return (d["is_noise"], -d["reach"], d["qualified_name"], d["id"])


def in_clause(n: int) -> str:
    return ",".join("?" * n)


def merge_edges(rows: list[sqlite3.Row]) -> dict[str, dict]:
    """Collapse several edges (calls + may_call) between the same pair into one entry."""
    out: dict[str, dict] = {}
    for r in rows:
        d = out.get(r["fid"])
        if d is None:
            d = out[r["fid"]] = {"row": r, "kinds": [], "count": 0, "lines": set(), "dispatch": False}
        if r["kind"] not in d["kinds"]:
            d["kinds"].append(r["kind"])
        d["count"] += r["count"]
        d["lines"].update(json.loads(r["lines_json"]))
        d["dispatch"] = d["dispatch"] or bool(r["dispatch"])
    return out


def edge_info(d: dict) -> dict:
    kinds = sorted(d["kinds"], key=CALL_KINDS.index) if set(d["kinds"]) <= set(CALL_KINDS) else d["kinds"]
    return {
        "kind": kinds[0],
        "kinds": kinds,
        "count": d["count"],
        "lines": sorted(d["lines"]),
        "dispatch": d["dispatch"],
    }


def summary_dict(row: sqlite3.Row) -> dict:
    """A summaries row as the API shows it (JSON columns decoded, internal cache key left out)."""
    d = dict(row)
    d.pop("function_id", None)
    d.pop("prompt_hash", None)
    for col, name in (
        ("side_effects_json", "side_effects"), ("mentions_json", "mentions"), ("reasons_json", "reasons"),
        ("change_risks_json", "change_risks"),
    ):
        if col in d:
            raw = d.pop(col)
            d[name] = json.loads(raw) if raw else []
    if "impact_json" in d:
        raw = d.pop("impact_json")
        d["impact"] = json.loads(raw) if raw else None
    if "verified" in d:
        d["verified"] = None if d["verified"] is None else bool(d["verified"])
    return d


def meta(conn: sqlite3.Connection) -> dict:
    out = {}
    for r in conn.execute("SELECT key, value FROM meta"):
        v = r["value"]
        try:
            out[r["key"]] = json.loads(v) if v and v[0] in "{[" else v
        except ValueError:
            out[r["key"]] = v
    return out


def get_function_row(conn: sqlite3.Connection, fid: str) -> sqlite3.Row:
    row = conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.id = ?", (fid,)).fetchone()
    if row is None:
        if conn.execute("SELECT 1 FROM fields WHERE id = ?", (fid,)).fetchone():
            raise HTTPException(404, f"'{fid}' is a field, not a function. Use /fields/{fid} instead.")
        raise HTTPException(404, f"No function with id '{fid}' in this repo.")
    return row


def github_base(repo_url: str | None) -> str | None:
    """Turn a git remote into an https GitHub URL (None for other hosts)."""
    if not repo_url:
        return None
    url = repo_url.strip()
    m = re.match(r"^git@github\.com:(.+?)(?:\.git)?/?$", url) or re.match(
        r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com/(.+?)(?:\.git)?/?$", url
    )
    return f"https://github.com/{m.group(1)}" if m else None


def read_source(repo_dir: str | None, rel_file: str, start: int, end: int) -> dict:
    """Read only this function's lines from the repo checkout on disk."""
    result = {"file": rel_file, "start": start, "end": end, "code": None, "available": False}
    if not repo_dir:
        return result
    root = Path(repo_dir).resolve()
    path = (root / rel_file).resolve()
    if root not in path.parents or not path.is_file():  # never read outside the repo
        return result
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return result
    result["code"] = "\n".join(lines[max(start - 1, 0) : end])
    result["available"] = True
    return result


# ---------------------------------------------------------------- app


def create_app(data_dir: Path | None = None) -> FastAPI:
    data_dir = Path(data_dir or os.environ.get("AEROTRACE_DATA") or DATA_DIR)
    app = FastAPI(title="aerotrace", description="Call trees and data access for legacy code.")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["GET"],
        allow_headers=["*"],
    )

    def get_db(repo: str) -> Iterator[sqlite3.Connection]:
        path = data_dir / f"{repo}.db"
        if not REPO_NAME_RE.match(repo) or not path.is_file():
            raise HTTPException(404, f"No repo called '{repo}' is loaded. Load one with: python -m server.store load data/{repo}.graph.json")
        try:
            conn = connect(path, readonly=True)
        except sqlite3.Error as e:
            raise HTTPException(404, f"The database for '{repo}' could not be opened ({e}).")
        try:
            yield conn
        finally:
            conn.close()

    DB = Depends(get_db)

    for path in sorted(data_dir.glob("*.db")):  # databases made by an older version get the new columns
        try:
            conn = sqlite3.connect(path)
            migrate(conn)
            conn.close()
        except sqlite3.Error:
            pass  # a damaged file is reported when someone asks for it

    def route(path: str, model, **kw):
        """GET route whose JSON shape is described by `model` in the OpenAPI schema.

        The function still returns a plain dict and it is sent as-is: running big trees through a
        response model (or FastAPI's generic encoder) cost more than the 200 ms budget."""

        def deco(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                return JSONResponse(fn(*args, **kwargs))

            app.get(path, response_model=None, responses={200: {"model": model}}, **kw)(wrapper)
            return fn

        return deco

    @app.exception_handler(sqlite3.Error)
    async def sqlite_error(_: Request, exc: sqlite3.Error):
        from fastapi.responses import JSONResponse

        return JSONResponse(status_code=503, content={"detail": f"The database for this repo could not be read ({exc})."})

    @route("/api/status", M.StatusResponse)
    def status() -> dict:
        return {"offline": offline_mode()}

    # ---- repos
    @route("/api/repos", list[M.RepoInfo])
    def list_repos():
        out = []
        for p in sorted(data_dir.glob("*.db")):
            try:
                conn = connect(p, readonly=True)
                try:
                    m = meta(conn)
                    summarized = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
                finally:
                    conn.close()
                out.append(
                    {
                        "name": p.stem,
                        "repo_url": m.get("repo_url"),
                        "commit_sha": m.get("commit_sha"),
                        "language": m.get("language"),
                        "extracted_at": m.get("extracted_at"),
                        "loaded_at": m.get("loaded_at"),
                        "stats": m.get("stats"),
                        "summaries": summarized,
                    }
                )
            except sqlite3.Error as e:
                out.append({"name": p.stem, "error": f"could not read this database ({e})"})
        return out

    # ---- entry points
    @route("/api/repos/{repo}/entry-points", list[M.EntryPoint])
    def entry_points(repo: str, limit: int = Query(100, ge=1, le=1000), conn=DB):
        ids = meta(conn).get("entry_points") or []
        out = []
        for rank, fid in enumerate(ids[:limit], start=1):
            row = conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.id = ?", (fid,)).fetchone()
            if row:
                out.append({"rank": rank, **brief(row)})
        return out

    # ---- search
    @route("/api/repos/{repo}/search", M.SearchResponse)
    def search(repo: str, q: str = Query("", max_length=200), limit: int = Query(20, ge=1, le=100), conn=DB) -> dict:
        pieces = re.findall(r"\w+", q)
        if not pieces:
            return {"query": q, "functions": [], "fields": []}
        ql = q.strip().lower()

        def tier(name: str, qname: str) -> int:
            n = name.lower()
            if n == ql or qname.lower() == ql:
                return 0
            if n.startswith(ql):
                return 1
            return 2 if ql in n else 3

        fn_rows, fld_rows = [], []
        long_pieces = [p for p in pieces if len(p) >= 3]  # the trigram index needs 3+ characters
        if long_pieces:
            match = " AND ".join(f'"{p}"' for p in long_pieces)
            fn_rows = conn.execute(
                f"""SELECT {FN_COLS}, bm25(functions_fts, 10, 5, 2, 1, 1) AS score
                    FROM functions_fts JOIN functions f ON f.rowid = functions_fts.rowid
                    LEFT JOIN summaries s ON s.function_id = f.id
                    WHERE functions_fts MATCH ? ORDER BY score LIMIT ?""",
                (match, limit * 5),
            ).fetchall()
            fld_rows = conn.execute(
                """SELECT f.*, bm25(fields_fts, 10, 5, 2, 1) AS score
                   FROM fields_fts JOIN fields f ON f.rowid = fields_fts.rowid
                   WHERE fields_fts MATCH ? ORDER BY score LIMIT ?""",
                (match, limit * 5),
            ).fetchall()
        else:  # very short query: plain substring match on names
            like = f"%{q.strip()}%"
            fn_rows = conn.execute(
                f"SELECT {FN_COLS}, 0 AS score FROM {FN_FROM} WHERE f.name LIKE ? OR f.qualified_name LIKE ? LIMIT ?",
                (like, like, limit * 5),
            ).fetchall()
            fld_rows = conn.execute(
                "SELECT f.*, 0 AS score FROM fields f WHERE f.name LIKE ? OR f.qualified_name LIKE ? LIMIT ?",
                (like, like, limit * 5),
            ).fetchall()

        fns = sorted(
            fn_rows, key=lambda r: (tier(r["name"], r["qualified_name"]), bool(r["is_noise"]), r["score"], -r["reach"])
        )[:limit]
        flds = sorted(fld_rows, key=lambda r: (tier(r["name"], r["qualified_name"]), r["score"]))[:limit]
        return {
            "query": q,
            "functions": [brief(r) for r in fns],
            "fields": [
                {k: r[k] for k in ("id", "name", "qualified_name", "container", "type", "file", "line")} for r in flds
            ],
        }

    # ---- one function
    def callers_of(conn, fid, kinds=CALL_KINDS):
        rows = conn.execute(
            f"""SELECT e.kind, e.count, e.lines_json, e.dispatch, {FN_COLS}, f.id AS fid
                FROM edges e JOIN functions f ON f.id = e.source {SUM_JOIN}
                WHERE e.target = ? AND e.kind IN ({in_clause(len(kinds))})""",
            (fid, *kinds),
        ).fetchall()
        merged = merge_edges(rows)
        return sorted(({**brief(d["row"]), **edge_info(d)} for d in merged.values()), key=fn_order)

    def callees_of(conn, fid, kind):
        rows = conn.execute(
            f"""SELECT e.kind, e.count, e.lines_json, e.dispatch, {FN_COLS}, f.id AS fid
                FROM edges e JOIN functions f ON f.id = e.target {SUM_JOIN}
                WHERE e.source = ? AND e.kind = ?""",
            (fid, kind),
        ).fetchall()
        return sorted(({**brief(d["row"]), **edge_info(d)} for d in merge_edges(rows).values()), key=fn_order)

    def fields_of(conn, fid, kind):
        rows = conn.execute(
            """SELECT f.id, f.name, f.qualified_name, f.container, f.type, f.file, f.line, e.count, e.lines_json
               FROM edges e JOIN fields f ON f.id = e.target WHERE e.source = ? AND e.kind = ?
               ORDER BY f.qualified_name""",
            (fid, kind),
        ).fetchall()
        return [
            {**{k: r[k] for k in ("id", "name", "qualified_name", "container", "type", "file", "line")},
             "count": r["count"], "lines": json.loads(r["lines_json"])}
            for r in rows
        ]

    @route("/api/repos/{repo}/functions/{fid}", M.FunctionDetail)
    def function_detail(repo: str, fid: str, conn=DB) -> dict:
        row = get_function_row(conn, fid)
        node = json.loads(conn.execute("SELECT node_json FROM functions WHERE id = ?", (fid,)).fetchone()[0])
        m = meta(conn)
        node["original_id"] = node.pop("id")
        node["id"] = fid
        s = conn.execute("SELECT * FROM summaries WHERE function_id = ?", (fid,)).fetchone()
        summary = summary_dict(s) if s else (
            {"short": node.get("summary"), "detailed": None, "model": None, "created_at": None}
            if node.get("summary") else None
        )
        node["summary"] = summary["short"] if summary else None

        indirect = node.get("indirect_calls") or []
        base = github_base(m.get("repo_url"))
        sha = m.get("commit_sha")
        permalink = (
            f"{base}/blob/{sha}/{row['file']}#L{row['line_start']}-L{row['line_end']}" if base and sha else None
        )
        overrides = conn.execute(
            f"""SELECT {FN_COLS} FROM edges e JOIN functions f ON f.id = e.target {SUM_JOIN}
                WHERE e.source = ? AND e.kind = 'overrides'""", (fid,)).fetchall()
        overridden_by = conn.execute(
            f"""SELECT {FN_COLS} FROM edges e JOIN functions f ON f.id = e.source {SUM_JOIN}
                WHERE e.target = ? AND e.kind = 'overrides'""", (fid,)).fetchall()
        return {
            "node": node,
            "summary": summary,
            "callers": callers_of(conn, fid),
            "callees": {"calls": callees_of(conn, fid, "calls"), "may_call": callees_of(conn, fid, "may_call")},
            "fields_read": fields_of(conn, fid, "reads"),
            "fields_written": fields_of(conn, fid, "writes"),
            "indirect_calls": {"count": len(indirect), "lines": indirect},
            "overrides": sorted((brief(r) for r in overrides), key=fn_order),
            "overridden_by": sorted((brief(r) for r in overridden_by), key=fn_order),
            "source": read_source(resolve_repo_dir(m.get("repo_dir"), repo), row["file"], row["line_start"], row["line_end"]),
            "github_permalink": permalink,
            "flags": function_flags(node, len(callees_of(conn, fid, "may_call"))),
        }

    # ---- tree
    @route("/api/repos/{repo}/tree", M.TreeResponse)
    def tree(
        repo: str,
        root: str,
        direction: Literal["down", "up"] = "down",
        depth: int = Query(2, ge=1, le=8),
        include_noise: bool = False,
        include_may_call: bool = True,
        conn=DB,
    ) -> dict:
        root_row = get_function_row(conn, root)
        kinds = CALL_KINDS if include_may_call else ("calls",)
        near, far = ("source", "target") if direction == "down" else ("target", "source")
        sql = f"""SELECT e.kind, e.count, e.lines_json, e.dispatch, {FN_COLS}, f.id AS fid
                  FROM edges e JOIN functions f ON f.id = e.{far} {SUM_JOIN}
                  WHERE e.{near} = ? AND e.kind IN ({in_clause(len(kinds))})"""
        budget = [MAX_TREE_NODES]
        truncated = [False]

        def children_of(fid: str) -> list[tuple[dict, dict]]:
            merged = merge_edges(conn.execute(sql, (fid, *kinds)).fetchall())
            items = [({**brief(d["row"])}, edge_info(d)) for d in merged.values()]
            if not include_noise:
                items = [it for it in items if not it[0]["is_noise"]]
            return sorted(items, key=lambda it: fn_order(it[0]))

        def build(row: dict, edge: dict | None, path_id: str, level: int, ancestors: tuple, cycle: bool) -> dict:
            node = {**row, "path_id": path_id, "edge": edge, "cycle": cycle, "children": []}
            budget[0] -= 1
            if cycle:
                node.update(has_more=False, child_count=0, hidden_count=0)
                return node  # already in its own ancestor chain: stop here
            kids = children_of(row["id"])
            node["child_count"] = len(kids)
            node["hidden_count"] = 0
            node["has_more"] = False
            if not kids:
                return node
            if level >= depth or budget[0] <= 0:
                node["has_more"] = True  # the UI can ask for this branch with root=<id>
                truncated[0] = truncated[0] or (level < depth)
                return node
            shown = kids[:MAX_CHILDREN]
            node["hidden_count"] = len(kids) - len(shown)
            chain = ancestors + (row["id"],)
            for i, (crow, cedge) in enumerate(shown):
                node["children"].append(
                    build(crow, cedge, f"{path_id}/{i}", level + 1, chain, crow["id"] in chain)
                )
            return node

        return {
            "repo": repo,
            "direction": direction,
            "depth": depth,
            "include_noise": include_noise,
            "include_may_call": include_may_call,
            "root": build(brief(root_row), None, "root", 0, (), False),
            "node_count": MAX_TREE_NODES - budget[0],
            "truncated": truncated[0],
        }

    # ---- flow stories, class and file roll-ups
    @route("/api/repos/{repo}/story/{fid}", M.StoryResponse)
    def story(repo: str, fid: str, conn=DB) -> dict:
        row = get_function_row(conn, fid)
        try:
            st = conn.execute("SELECT * FROM flow_stories WHERE function_id = ?", (fid,)).fetchone()
        except sqlite3.OperationalError:  # database from before stories existed
            st = None
        if st is None:
            raise HTTPException(
                404,
                f"No story for '{row['qualified_name']}' yet. Stories are written for entry points "
                f"when you run: python -m extractor.common.explain {repo}",
            )
        return {
            "function": brief(row),
            "story": st["story"],
            "steps": json.loads(st["steps_json"]) if st["steps_json"] else [],
            "model": st["model"],
            "created_at": st["created_at"],
        }

    def rollup_rows(conn, kind: str) -> list[sqlite3.Row]:
        try:
            return conn.execute("SELECT * FROM rollups WHERE kind = ? ORDER BY key", (kind,)).fetchall()
        except sqlite3.OperationalError:
            return []

    def rollup_dict(r: sqlite3.Row) -> dict:
        return {k: r[k] for k in ("key", "short", "detailed", "model", "created_at")}

    def add_rollup_routes(kind: str, plural: str) -> None:
        @route(f"/api/repos/{{repo}}/{plural}", list[M.Rollup], name=f"list_{plural}")
        def list_rollups(repo: str, q: str = Query("", max_length=200), conn=DB):
            rows = [rollup_dict(r) for r in rollup_rows(conn, kind)]
            return [r for r in rows if q.lower() in r["key"].lower()] if q else rows

        @route(f"/api/repos/{{repo}}/{plural}/{{key:path}}", M.RollupDetail, name=f"get_{kind}_summary")
        def get_rollup(repo: str, key: str, conn=DB) -> dict:
            hit = next((r for r in rollup_rows(conn, kind) if r["key"] == key), None)
            if hit is None:
                raise HTTPException(404, f"No summary for {kind} '{key}'. Summaries are written by the explain step.")
            out = rollup_dict(hit)
            if kind == "class":
                rows = conn.execute(
                    f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.qualified_name LIKE ? AND f.container IS NOT NULL",
                    (key + "::%",),
                ).fetchall()
                rows = [r for r in rows if r["qualified_name"].rsplit("::", 1)[0] == key]
            else:
                rows = conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.file = ?", (key,)).fetchall()
            out["functions"] = sorted((brief(r) for r in rows), key=fn_order)
            return out

    add_rollup_routes("class", "classes")
    add_rollup_routes("file", "files")

    # ---- one field
    @route("/api/repos/{repo}/fields/{fid}", M.FieldDetail)
    def field_detail(repo: str, fid: str, conn=DB) -> dict:
        row = conn.execute("SELECT * FROM fields WHERE id = ?", (fid,)).fetchone()
        if row is None:
            if conn.execute("SELECT 1 FROM functions WHERE id = ?", (fid,)).fetchone():
                raise HTTPException(404, f"'{fid}' is a function, not a field. Use /functions/{fid} instead.")
            raise HTTPException(404, f"No field with id '{fid}' in this repo.")
        node = json.loads(row["node_json"])
        node["original_id"] = node.pop("id")
        node["id"] = fid

        def users(kind: str):
            rows = conn.execute(
                f"""SELECT e.kind, e.count, e.lines_json, e.dispatch, {FN_COLS}, f.id AS fid
                    FROM edges e JOIN functions f ON f.id = e.source {SUM_JOIN} WHERE e.target = ? AND e.kind = ?""",
                (fid, kind),
            ).fetchall()
            return sorted(({**brief(d["row"]), **edge_info(d)} for d in merge_edges(rows).values()), key=fn_order)

        return {"field": node, "readers": users("reads"), "writers": users("writes")}

    # ---- impact
    @route("/api/repos/{repo}/impact/{fid}", M.ImpactResponse)
    def impact(repo: str, fid: str, depth: int = Query(5, ge=1, le=20), conn=DB) -> dict:
        root_row = get_function_row(conn, fid)
        seen = {fid}
        frontier = [fid]
        levels = []
        for distance in range(1, depth + 1):
            found: dict[str, list] = {}
            for i in range(0, len(frontier), 400):
                chunk = frontier[i : i + 400]
                rows = conn.execute(
                    f"""SELECT e.source AS src, e.target AS tgt, e.kind
                        FROM edges e WHERE e.target IN ({in_clause(len(chunk))}) AND e.kind IN ('calls','may_call')""",
                    chunk,
                ).fetchall()
                for r in rows:
                    if r["src"] not in seen:
                        found.setdefault(r["src"], []).append((r["tgt"], r["kind"]))
            if not found:
                break
            ids = list(found)
            seen.update(ids)
            fns = []
            for i in range(0, len(ids), 400):
                chunk = ids[i : i + 400]
                fns += conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.id IN ({in_clause(len(chunk))})", chunk).fetchall()
            items = []
            for r in fns:
                d = brief(r)
                d["calls"] = sorted({t for t, _ in found[r["id"]]})  # which function(s) one step closer it calls
                d["via"] = sorted({k for _, k in found[r["id"]]})
                items.append(d)
            levels.append({"distance": distance, "functions": sorted(items, key=fn_order)})
            frontier = ids
        root = brief(root_row)
        total = sum(len(lv["functions"]) for lv in levels)
        return {
            "root": root,
            "max_depth": depth,
            "total": total,
            "levels": levels,
            **change_impact(conn, fid, root, levels, seen, frontier),
        }

    def change_impact(conn, fid: str, root: dict, levels: list[dict], seen: set[str], frontier: list[str]) -> dict:
        """Severity, how each function is affected, and rough effort. From the graph alone (no AI), with the
        same rules as the explain step, so this tab and the Explain tab agree."""
        # keep counting callers past `depth` (ids only), so the overall reason matches the Explain tab
        upstream = set(seen)
        while frontier:
            nxt = []
            for i in range(0, len(frontier), 400):
                chunk = frontier[i : i + 400]
                for r in conn.execute(
                    f"SELECT source FROM edges WHERE target IN ({in_clause(len(chunk))}) AND kind IN ('calls','may_call')", chunk
                ):
                    if r["source"] not in upstream:
                        upstream.add(r["source"])
                        nxt.append(r["source"])
            frontier = nxt
        upstream.discard(fid)

        # functions that read data this one changes: they use it directly, like a caller
        readers: dict[str, set[str]] = {}
        for r in conn.execute(
            """SELECT r.source AS reader, fl.name AS field FROM edges w
               JOIN edges r ON r.target = w.target AND r.kind = 'reads'
               JOIN fields fl ON fl.id = w.target
               WHERE w.source = ? AND w.kind = 'writes' AND r.source != ?""",
            (fid, fid),
        ):
            readers.setdefault(r["reader"], set()).add(r["field"])

        found: dict[str, tuple[int, dict]] = {f["id"]: (lv["distance"], f) for lv in levels for f in lv["functions"]}
        missing = [i for i in readers if i not in found]
        for i in range(0, len(missing), 400):
            chunk = missing[i : i + 400]
            for r in conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.id IN ({in_clause(len(chunk))})", chunk):
                found[r["id"]] = (1, brief(r))
        name_of = {i: d["name"] for i, (_, d) in found.items()} | {fid: root["name"]}

        affected = []
        for i, (distance, d) in found.items():
            how = []
            if distance == 1 and d.get("via"):
                how.append("May call it, through a virtual call" if d["via"] == ["may_call"] else "Calls it directly")
            elif distance > 1:
                how.append("Calls it through " + ", ".join(dict.fromkeys(name_of.get(c, "another function") for c in d["calls"][:3])))
            if i in readers:
                how.append(f"Reads {', '.join(sorted(readers[i]))}, which this function changes")
                distance = 1
            severity, why = X.affected_severity(distance, d["is_entry_point"])
            base = {k: v for k, v in d.items() if k not in ("calls", "via")}
            affected.append({
                **base, "distance": distance, "how": how, "severity": severity, "severity_reason": why,
                "effort_hours": X.effort_hours(severity, d["loc"]),
            })
        rank = {"high": 0, "medium": 1, "low": 2}
        affected.sort(key=lambda a: (rank[a["severity"]], a["distance"], a["file"], a["qualified_name"]))

        direct = [a for a in affected if a["distance"] == 1]
        direct_files = len({a["file"] for a in direct} - {root["file"]})
        severity, reason = X.impact_level(len(direct), direct_files, len(upstream | set(readers)), root["is_entry_point"])
        effort = X.effort_range(root["loc"], sum(a["effort_hours"] for a in affected), len(affected) > len(direct))

        s = conn.execute("SELECT business_impact, model, verified FROM summaries WHERE function_id = ?", (fid,)).fetchone()
        text = s["business_impact"] if s else None
        return {
            "severity": severity,
            "severity_reason": reason,
            "effort": effort,
            "affected": affected,
            "business_impact": text,
            "business_impact_checked": None if not text or s["verified"] is None else bool(s["verified"]),
        }

    # ---- a path between two functions (used to add a data item's writers to the tree)
    @route("/api/repos/{repo}/path", M.PathResponse)
    def call_path(
        repo: str,
        source: str,
        target: str,
        direction: Literal["down", "up"] = "down",
        max_depth: int = Query(12, ge=1, le=30),
        conn=DB,
    ) -> dict:
        get_function_row(conn, source)
        target_row = get_function_row(conn, target)
        near, far = ("source", "target") if direction == "down" else ("target", "source")
        parent: dict[str, str | None] = {source: None}
        frontier = [source]
        found = source == target
        for _ in range(max_depth):
            if found or not frontier or len(parent) > 50_000:
                break
            nxt: list[str] = []
            for i in range(0, len(frontier), 400):
                chunk = frontier[i : i + 400]
                for r in conn.execute(
                    f"""SELECT e.{near} AS a, e.{far} AS b FROM edges e
                        WHERE e.{near} IN ({in_clause(len(chunk))}) AND e.kind IN ('calls','may_call')""",
                    chunk,
                ):
                    if r["b"] not in parent:
                        parent[r["b"]] = r["a"]
                        nxt.append(r["b"])
            found = target in parent
            frontier = nxt
        if not found:
            return {"found": False, "direction": direction, "path": []}
        chain, cur = [], target
        while cur is not None:
            chain.append(cur)
            cur = parent[cur]
        chain.reverse()
        rows = {
            r["id"]: r
            for r in conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.id IN ({in_clause(len(chain))})", chain)
        }
        return {"found": True, "direction": direction, "path": [brief(rows[i]) for i in chain if i in rows]}

    # ---- the data map: a class's (or file's) functions and the pieces of data they touch
    MAP_MAX_FUNCTIONS = 120
    MAP_MAX_FIELDS = 150

    @route("/api/repos/{repo}/datamap", M.DataMapResponse)
    def datamap(repo: str, fn: str, scope: Literal["class", "file"] | None = None, conn=DB) -> dict:
        row = get_function_row(conn, fn)
        qname, file = row["qualified_name"], row["file"]
        cls = qname.rsplit("::", 1)[0] if row["container"] and "::" in qname else None
        kind = scope or ("class" if cls else "file")
        if kind == "class" and not cls:
            kind = "file"
        if kind == "class":
            rows = conn.execute(
                f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.container IS NOT NULL AND f.qualified_name LIKE ?", (cls + "::%",)
            ).fetchall()
            rows = [r for r in rows if r["qualified_name"].rsplit("::", 1)[0] == cls]
            key = cls
        else:
            rows = conn.execute(f"SELECT {FN_COLS} FROM {FN_FROM} WHERE f.file = ?", (file,)).fetchall()
            key = file
        rows.sort(key=lambda r: (-r["writes_count"], r["qualified_name"]))
        truncated = len(rows) > MAP_MAX_FUNCTIONS
        rows = rows[:MAP_MAX_FUNCTIONS]
        ids = [r["id"] for r in rows]

        links = []
        for i in range(0, len(ids), 400):
            chunk = ids[i : i + 400]
            links += conn.execute(
                f"""SELECT source, target, kind, count, lines_json FROM edges
                    WHERE source IN ({in_clause(len(chunk))}) AND kind IN ('reads','writes')""",
                chunk,
            ).fetchall()
        field_ids = list({l["target"] for l in links})
        counts: dict[str, dict[str, int]] = {}
        for i in range(0, len(field_ids), 400):
            chunk = field_ids[i : i + 400]
            for r in conn.execute(
                f"""SELECT target, kind, COUNT(DISTINCT source) AS n FROM edges
                    WHERE target IN ({in_clause(len(chunk))}) AND kind IN ('reads','writes') GROUP BY target, kind""",
                chunk,
            ):
                counts.setdefault(r["target"], {})[r["kind"]] = r["n"]
        fields = []
        for i in range(0, len(field_ids), 400):
            chunk = field_ids[i : i + 400]
            fields += conn.execute(f"SELECT * FROM fields WHERE id IN ({in_clause(len(chunk))})", chunk).fetchall()
        fields.sort(key=lambda f: (-counts.get(f["id"], {}).get("writes", 0), f["qualified_name"]))
        if len(fields) > MAP_MAX_FIELDS:
            truncated = True
            fields = fields[:MAP_MAX_FIELDS]
        keep = {f["id"] for f in fields}
        return {
            "scope": {"kind": kind, "key": key},
            "available": {"class_name": cls, "file": file},
            "functions": [brief(r) for r in rows],
            "fields": [
                {
                    **{k: f[k] for k in ("id", "name", "qualified_name", "container", "type", "file", "line")},
                    "writers": counts.get(f["id"], {}).get("writes", 0),
                    "readers": counts.get(f["id"], {}).get("reads", 0),
                }
                for f in fields
            ],
            "links": [
                {"function_id": l["source"], "field_id": l["target"], "kind": l["kind"], "count": l["count"], "lines": json.loads(l["lines_json"])}
                for l in links
                if l["target"] in keep
            ],
            "truncated": truncated,
        }

    return app


app = create_app()
