# aerotrace

Legacy code understanding tool. Reads a C/C++ repo (Ada later), builds a call graph plus a
data-access graph, explains each function in plain English with an LLM, and shows it all as an
interactive call tree.

Layout: `extractor/` (Python: `cpp/`, `ada/`, `common/`), `server/` (FastAPI), `web/` (React + Vite + TS),
`data/` and `repos/` (gitignored), `tests/` (pytest).

## Rules for all future work

- Never change the schema contract (`extractor/common/schema.py`) without being asked. Adding
  optional fields is allowed only when asked; never rename or remove fields.
- Every pipeline step must be runnable alone from the Makefile and must have a pytest test.
- Prefer correctness over coverage: if a call target cannot be resolved, record it as unresolved,
  never guess.
- Keep explanations and UI text in plain, everyday English.

## Commands

`make setup`, `make extract`, `make explain`, `make serve`, `make web`, `make test`.
