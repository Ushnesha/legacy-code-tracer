"""Storage layer: load a graph JSON into SQLite (data/<name>.db).

    python -m server.store load data/<name>.graph.json

Every node gets a short, URL-safe id (first 12 hex chars of sha1 of the original id). Clang
USRs contain characters like @ # < > * that break URLs; the original stays in `orig_id`.
The `summaries` table survives a reload so LLM work is never thrown away.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from extractor.common.config import PROJECT_ROOT
from extractor.common.schema import FieldNode, FileNode, FunctionNode, Graph, load_graph

DATA_DIR = PROJECT_ROOT / "data"

SCHEMA = """
CREATE TABLE functions (
    id TEXT PRIMARY KEY,              -- short id used by the API
    orig_id TEXT NOT NULL UNIQUE,     -- original id (clang USR for C++)
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    container TEXT,
    language TEXT NOT NULL,
    file TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    is_virtual INTEGER NOT NULL,
    is_pure_virtual INTEGER NOT NULL,
    is_entry_point INTEGER NOT NULL,
    is_noise INTEGER NOT NULL,
    noise_reason TEXT,
    loc INTEGER NOT NULL,
    fan_in INTEGER NOT NULL,
    fan_out INTEGER NOT NULL,
    reach INTEGER NOT NULL,
    summary TEXT,                     -- summary carried in the graph file, if any
    node_json TEXT NOT NULL,          -- the complete node, so no field is ever lost
    writes_count INTEGER NOT NULL DEFAULT 0,    -- distinct pieces of data it changes
    indirect_count INTEGER NOT NULL DEFAULT 0   -- calls through a pointer (target unknown)
);
CREATE TABLE fields (
    id TEXT PRIMARY KEY,
    orig_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    container TEXT,
    type TEXT NOT NULL,
    file TEXT NOT NULL,
    line INTEGER NOT NULL,
    node_json TEXT NOT NULL
);
CREATE TABLE files (
    id TEXT PRIMARY KEY,
    orig_id TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    language TEXT NOT NULL
);
CREATE TABLE edges (
    source TEXT NOT NULL,             -- short ids
    target TEXT NOT NULL,
    kind TEXT NOT NULL,
    count INTEGER NOT NULL,
    lines_json TEXT NOT NULL,
    dispatch INTEGER NOT NULL DEFAULT 0,
    inferred INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_edges_source ON edges(source, kind);
CREATE INDEX idx_edges_target ON edges(target, kind);
CREATE TABLE summaries (
    function_id TEXT PRIMARY KEY,
    short TEXT,
    detailed TEXT,
    model TEXT,                       -- "template" when no LLM was used
    prompt_hash TEXT,                 -- cache key: source + callee summaries + prompt version + model
    created_at TEXT,
    inputs TEXT,
    outputs TEXT,
    side_effects_json TEXT,
    mentions_json TEXT,
    verified INTEGER,                 -- 1 = every claim checked out against the graph
    reasons_json TEXT                 -- why it is not verified
);
CREATE TABLE flow_stories (
    function_id TEXT PRIMARY KEY,     -- an entry point
    story TEXT NOT NULL,              -- numbered "what happens when it runs" steps
    steps_json TEXT,
    model TEXT,
    prompt_hash TEXT,
    created_at TEXT
);
CREATE TABLE rollups (
    kind TEXT NOT NULL,               -- "class" or "file"
    key TEXT NOT NULL,                -- qualified class name or repo-relative path
    short TEXT,
    detailed TEXT,
    model TEXT,
    prompt_hash TEXT,
    created_at TEXT,
    PRIMARY KEY (kind, key)
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE VIRTUAL TABLE functions_fts USING fts5(
    name, qualified_name, container, file, summary, tokenize='trigram'
);
CREATE VIRTUAL TABLE fields_fts USING fts5(
    name, qualified_name, container, file, tokenize='trigram'
);
"""


def resolve_repo_dir(stored: str | None, repo_name: str) -> str | None:
    """Where the repo's files are on this machine: the stored path if it exists, else repos/<name>."""
    if stored:
        p = Path(stored)
        p = p if p.is_absolute() else PROJECT_ROOT / p
        if p.is_dir():
            return str(p)
    fallback = PROJECT_ROOT / "repos" / repo_name
    return str(fallback) if fallback.is_dir() else (stored or None)


def short_id(original: str) -> str:
    return hashlib.sha1(original.encode("utf-8")).hexdigest()[:12]


def connect(path: Path, readonly: bool = False) -> sqlite3.Connection:
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def db_path_for(repo_name: str, data_dir: Path = DATA_DIR) -> Path:
    return data_dir / f"{repo_name}.db"


def rebuild_fts(conn: sqlite3.Connection) -> None:
    """Refresh the search index (call again after summaries change)."""
    conn.execute("DELETE FROM functions_fts")
    conn.execute(
        """INSERT INTO functions_fts(rowid, name, qualified_name, container, file, summary)
           SELECT f.rowid, f.name, f.qualified_name, COALESCE(f.container, ''), f.file,
                  COALESCE(s.short, f.summary, '')
           FROM functions f LEFT JOIN summaries s ON s.function_id = f.id"""
    )
    conn.execute("DELETE FROM fields_fts")
    conn.execute(
        """INSERT INTO fields_fts(rowid, name, qualified_name, container, file)
           SELECT rowid, name, qualified_name, COALESCE(container, ''), file FROM fields"""
    )


LLM_TABLES = ("summaries", "flow_stories", "rollups")  # work that costs money: survives a reload

_EXTRA_SUMMARY_COLUMNS = {
    "inputs": "TEXT",
    "outputs": "TEXT",
    "side_effects_json": "TEXT",
    "mentions_json": "TEXT",
    "verified": "INTEGER",
    "reasons_json": "TEXT",
}


def migrate(conn: sqlite3.Connection) -> None:
    """Bring a database made by an older version up to the current tables (idempotent)."""
    have = {r[1] for r in conn.execute("PRAGMA table_info(summaries)")}
    for col, typ in _EXTRA_SUMMARY_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE summaries ADD COLUMN {col} {typ}")
    fn_cols = {r[1] for r in conn.execute("PRAGMA table_info(functions)")}
    if fn_cols and "writes_count" not in fn_cols:
        conn.execute("ALTER TABLE functions ADD COLUMN writes_count INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE functions ADD COLUMN indirect_count INTEGER NOT NULL DEFAULT 0")
        conn.execute(
            """UPDATE functions SET
                 writes_count = COALESCE(json_extract(node_json, '$.fields_written_count'), 0),
                 indirect_count = COALESCE(json_array_length(json_extract(node_json, '$.indirect_calls')), 0)"""
        )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS flow_stories (
            function_id TEXT PRIMARY KEY, story TEXT NOT NULL, steps_json TEXT,
            model TEXT, prompt_hash TEXT, created_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS rollups (
            kind TEXT NOT NULL, key TEXT NOT NULL, short TEXT, detailed TEXT,
            model TEXT, prompt_hash TEXT, created_at TEXT, PRIMARY KEY (kind, key))"""
    )
    conn.commit()


def _old_llm_rows(path: Path) -> dict[str, tuple[list[str], list[tuple]]]:
    """Rows of the LLM tables from the database being replaced: {table: (columns, rows)}."""
    out: dict[str, tuple[list[str], list[tuple]]] = {}
    if not path.exists():
        return out
    try:
        old = sqlite3.connect(path)
        for t in LLM_TABLES:
            try:
                cur = old.execute(f"SELECT * FROM {t}")
                out[t] = ([d[0] for d in cur.description], cur.fetchall())
            except sqlite3.Error:
                continue
        old.close()
    except sqlite3.Error:
        pass
    return out


def load_into_db(graph: Graph, db_path: Path, repo_dir: Path | None = None) -> dict:
    """(Re)build the database for one graph. Returns a few counts."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = db_path.with_name(db_path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    old_rows = _old_llm_rows(db_path)

    ids: dict[str, str] = {}  # original id -> short id
    owner: dict[str, str] = {}
    for n in graph.nodes:
        s = short_id(n.id)
        if s in owner and owner[s] != n.id:
            raise ValueError(f"short id collision: {owner[s]!r} and {n.id!r} both hash to {s}")
        owner[s] = n.id
        ids[n.id] = s

    conn = connect(tmp)
    try:
        conn.executescript(SCHEMA)
        for n in graph.nodes:
            sid = ids[n.id]
            blob = n.model_dump_json()
            if isinstance(n, FunctionNode):
                conn.execute(
                    "INSERT INTO functions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sid, n.id, n.name, n.qualified_name, n.container, n.language, n.file,
                        n.line_start, n.line_end, int(n.is_virtual), int(n.is_pure_virtual),
                        int(n.is_entry_point), int(n.is_noise), n.noise_reason, n.loc,
                        n.fan_in, n.fan_out, n.reach, n.summary, blob,
                        n.fields_written_count, len(n.indirect_calls),
                    ),
                )
            elif isinstance(n, FieldNode):
                conn.execute(
                    "INSERT INTO fields VALUES (?,?,?,?,?,?,?,?,?)",
                    (sid, n.id, n.name, n.qualified_name, n.container, n.type, n.file, n.line, blob),
                )
            elif isinstance(n, FileNode):
                conn.execute("INSERT INTO files VALUES (?,?,?,?)", (sid, n.id, n.path, n.language))

        dropped = 0
        rows = []
        for e in graph.edges:
            if e.source not in ids or e.target not in ids:
                dropped += 1
                continue
            rows.append(
                (ids[e.source], ids[e.target], e.kind, e.count, json.dumps(e.lines), int(e.dispatch), int(e.inferred))
            )
        conn.executemany("INSERT INTO edges VALUES (?,?,?,?,?,?,?)", rows)

        valid = {r[0] for r in conn.execute("SELECT id FROM functions")}
        kept = 0
        for table, (cols, old) in old_rows.items():
            new_cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            use = [c for c in cols if c in new_cols]
            idx = [cols.index(c) for c in use]
            for r in old:
                if table != "rollups" and r[cols.index("function_id")] not in valid:
                    continue  # the function is gone from the new graph
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({','.join(use)}) VALUES ({','.join('?' * len(use))})",
                    [r[i] for i in idx],
                )
                kept += table == "summaries"

        repo_dir = repo_dir or PROJECT_ROOT / "repos" / graph.repo_name
        try:  # keep it relative when it is inside the project, so a database made here still works elsewhere
            repo_dir = repo_dir.resolve().relative_to(PROJECT_ROOT)
        except ValueError:
            pass
        meta = {
            "repo_name": graph.repo_name,
            "repo_url": graph.repo_url,
            "commit_sha": graph.commit_sha,
            "language": graph.language,
            "extracted_at": graph.extracted_at.isoformat(),
            "loaded_at": datetime.now(timezone.utc).isoformat(),
            "repo_dir": str(repo_dir),
            "stats": graph.stats.model_dump(),
            "class_bases": graph.class_bases,
            "class_docs": graph.class_docs,
            "entry_points": [ids[i] for i in graph.entry_points if i in ids],
        }
        conn.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [(k, v if isinstance(v, str) or v is None else json.dumps(v)) for k, v in meta.items()],
        )
        rebuild_fts(conn)
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, db_path)
    return {"edges": len(rows), "dropped_edges": dropped, "kept_summaries": kept}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m server.store", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load", help="load a graph JSON into data/<name>.db")
    ld.add_argument("graph")
    ld.add_argument("--db", help="database path (default: <graph folder>/<repo_name>.db)")
    ld.add_argument("--repo-dir", help="where the repo is checked out, for source code (default: repos/<repo_name>)")
    args = ap.parse_args(argv)

    graph = load_graph(args.graph)
    db = Path(args.db) if args.db else Path(args.graph).resolve().parent / f"{graph.repo_name}.db"
    info = load_into_db(graph, db, Path(args.repo_dir).resolve() if args.repo_dir else None)
    s = graph.stats
    print(f"loaded {args.graph} -> {db}: {s.nodes}, {info['edges']} edges ({info['dropped_edges']} dropped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
