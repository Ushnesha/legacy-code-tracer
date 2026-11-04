# AeroTrace

AeroTrace helps engineers understand legacy C, C++, and Ada code. It extracts function calls and data access, builds a graph, and presents it as an interactive tree with source code, optional AI explanations, and change-impact analysis.

![AeroTrace exploring JSBSim's call tree](docs/screenshots/readme.png)

## Features

- Explore what a function calls and which functions call it, expanding one level at a time.
- See possible targets of virtual calls and distinguish them from direct calls.
- Inspect source code alongside the fields a function reads or writes.
- Select a field to highlight its readers and writers, or view a class's data-access map.
- Review callers and shared-data dependencies that could be affected by a change; export impact results.
- Generate cached explanations, class/file summaries, and entry-point stories with an LLM. Explanations that fail checks against the graph are flagged for review.
- Run without an AI API connection, retaining saved explanations and using templates where available.
- Follow a guided JSBSim tour in the web interface.

## Architecture

```text
C/C++ source -- libclang ---------------------+
                                             |
Ada source --- libadalang / tree-sitter -------+--> shared graph JSON
                                                     |
                                              enrich and load
                                                     |
                                                 SQLite
                                                     |
                                      optional LLM explanation pass
                                                     |
                                             FastAPI REST API
                                                     |
                                        React + TypeScript + Vite
                                        React Flow + ELK layout
```

The C/C++ extractor parses files in parallel and caches results using source and compiler inputs. Enrichment adds possible virtual-call targets, tags helper functions, and identifies entry points. The explanation pass processes callees before callers, groups cycles, and checks named dependencies and side effects against the extracted graph. The API serves the resulting SQLite databases to the web interface.

Ada extraction uses a normalized code model before converting to the shared graph. `libadalang` provides semantic analysis when installed; the tree-sitter fallback marks its links as uncertain and does not extract reads or writes.

## Requirements

- Python 3.11 or newer and `uv`.
- Node.js 20 or newer and pnpm. The web package declares `pnpm@9.15.9`.
- Git and Make.
- A working C/C++ toolchain and system headers for the code being analyzed. On macOS, install the Xcode Command Line Tools if headers are missing.
- Optional: `libadalang` and the target Ada project's dependencies for semantic Ada extraction. The default Python dependencies include the tree-sitter Ada backend.
- Optional: Docker with Docker Compose for the container workflow.

Run the commands below from this directory, which contains `Makefile` and `pyproject.toml`.

## Quick start

```bash
make setup
cp -n .env.example .env  # preserve any existing local configuration
AEROTRACE_OFFLINE=1 make demo REPO=jsbsim
```

Open <http://localhost:5173/?repo=jsbsim> and select **Tour**. The demo clones JSBSim if needed, extracts and enriches its graph, loads SQLite, prepares offline explanations, and starts the API and web servers. Press `Ctrl+C` to stop them.

`AEROTRACE_OFFLINE=1` disables AI API calls. The first run still needs network access to install dependencies and clone the sample repository. Without saved AI output, some functions show templates or “No explanation yet.” Graphs, databases, caches, and cloned target repositories are generated locally; a fresh checkout needs this preparation step.

For a smaller sample, use `REPO=tinyxml2`. To prepare a sample without starting servers:

```bash
AEROTRACE_OFFLINE=1 make demo REPO=tinyxml2 ARGS="--no-serve"
```

## Analyze another C/C++ repository

Place or clone the source under `repos/<name>`, then run each pipeline stage:

```bash
make extract REPO=repos/my-project
make enrich REPO=repos/my-project
make load REPO=repos/my-project
make explain REPO=my-project ARGS="--offline"
```

The graph is written to `data/my-project.graph.json` and the database to `data/my-project.db`. Start the API and frontend in separate terminals:

```bash
AEROTRACE_OFFLINE=1 make serve
```

```bash
make web
```

Open <http://localhost:5173> and choose the repository. The API runs at <http://localhost:8000>; its interactive documentation is at <http://localhost:8000/docs>.

For better C/C++ analysis, supply the directory containing the target's `compile_commands.json`:

```bash
make extract REPO=repos/my-project ARGS="--build-dir repos/my-project/build"
```

Other extractor options include `--jobs`, `--no-cache`, `--exclude` (repeatable), and `--config`. Inspect all options with `uv run python -m extractor.cpp --help`.

Repository configuration uses `aerotrace.yml` inside the target repository, `data/<name>.yml`, or `repos-config/<name>.yml`, in that order after an explicit `--config` path. Supported settings include `exclude`, `noise_classes`, `noise_name_patterns`, `entry_points`, and `url`. A configured `url` lets `make demo REPO=<name>` clone a missing target; this demo runner uses the C/C++ extractor.

## Analyze Ada

Use the Ada extractor explicitly; the remaining stages consume the same graph format:

```bash
make extract REPO=repos/my-ada-project EXTRACTOR=ada ARGS="--parser treesitter"
make enrich REPO=repos/my-ada-project
make load REPO=repos/my-ada-project
```

For semantic analysis with `libadalang` available, use `ARGS="--parser libadalang --project repos/my-ada-project/project.gpr"`. If no parser is specified, the extractor chooses `libadalang` when importable, otherwise tree-sitter. `--project` is repeatable; `--all-projects` selects project variants, and `--ncm-out <path>` also saves the normalized code model.

## AI explanations and configuration

Credentials are optional for extraction, graph browsing, and offline use. Copy the supplied [`.env.example`](.env.example) to a local `.env` if needed. Keep real keys in your shell environment or `.env`; never commit them. The demo and explanation commands load `.env` automatically, and variables already exported in the shell take precedence. Other commands, including `make serve`, need variables exported in their environment.

The explanation layer supports Anthropic and services compatible with the OpenAI Chat Completions API. Select a provider and configure model names accepted by that service. Both built-in model defaults are currently `qwen3-235b-a22b-instruct-2507`, so override both when using another model or provider.

| Variable | Purpose |
| --- | --- |
| `AEROTRACE_OFFLINE` | `1`, `true`, or `yes` disables AI calls in the explanation workflow. |
| `AEROTRACE_PROVIDER` | Explicit provider: `anthropic` or `openai`. Otherwise, credentials determine the choice; Anthropic takes precedence when both are present. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | Credentials for the Anthropic client. |
| `OPENAI_API_KEY` | Credentials for the OpenAI-compatible client. |
| `OPENAI_BASE_URL` | Endpoint for an alternative OpenAI-compatible service. |
| `AEROTRACE_MODEL` | Model for general explanations, rollups, and stories. |
| `AEROTRACE_FAST_MODEL` | Model for short functions. |
| `AEROTRACE_EFFORT` | Anthropic effort setting; defaults to `low`. |
| `AEROTRACE_DATA` | Alternate database directory for the API and explanation command. |
| `API_URL` | API proxy target for Vite; defaults to `http://localhost:8000`. |

After configuring credentials and models, explicitly enable online explanations:

```bash
AEROTRACE_OFFLINE=0 make explain REPO=jsbsim ARGS="--only-reachable-from-entry-points --max 30"
```

The command shows a token/cost estimate and asks before sending requests. Unknown model prices produce token estimates only. `--max` limits functions needing AI calls; use `--skip-rollups --skip-stories` to omit additional class/file summaries and entry-point stories. Results are cached in SQLite and remain available offline. Source snippets and graph context are sent to the configured provider when online explanations run.

## Docker

Prepare at least one database first, for example with `AEROTRACE_OFFLINE=1 make demo REPO=jsbsim ARGS="--no-serve"`. Then run:

```bash
docker compose up --build
```

Open <http://localhost:5173/?repo=jsbsim>. Compose mounts `data/` and `repos/`, exposes ports 8000 and 5173, and defaults to offline mode. Keep the source repository in `repos/` for the Code tab. The containers serve existing data; they do not run the extraction pipeline.

## Development and tests

```bash
make test                 # pytest: extraction, enrichment, explanation, API, demo
make test-web             # Vitest: frontend logic and layout
make api-types            # regenerate TypeScript API types from FastAPI's schema
cd web && pnpm build     # TypeScript checks and production frontend build
```

Some Python integration tests clone JSBSim and tinyxml2 when missing and require their build dependencies. Tests disable real credentials and use fake LLM responses; Ada semantic tests skip when `libadalang` is unavailable.

Browser tests need both sample databases. Prepare them and install Chromium once:

```bash
AEROTRACE_OFFLINE=1 make demo REPO=tinyxml2 ARGS="--no-serve"
AEROTRACE_OFFLINE=1 make demo REPO=jsbsim ARGS="--no-serve"
make e2e-setup
make e2e
```

Playwright starts the API and web development servers, or reuses servers already running on their configured ports.

## Project layout

```text
extractor/cpp/       libclang parser, compiler flags, parallel extraction and cache
extractor/ada/       libadalang and tree-sitter backends, model-to-graph conversion
extractor/common/    shared graph schema, configuration, enrichment and explanations
aerotrace/contracts/ normalized code model contracts and fixtures
server/             FastAPI routes, response models and SQLite storage
web/                React interface, graph layout, unit tests and browser tests
repos-config/       sample repository URLs, exclusions and entry-point settings
scripts/            end-to-end demo runner
tests/              Python tests and Ada source fixtures
docs/               screenshots and demo material
data/               generated graphs, databases and extraction caches
repos/              locally cloned target repositories
```

## Known limitations

- C/C++ analysis depends on compiler flags, system headers, and target dependencies. Without a compile database, flags are inferred. Extraction stops when more than 5% of files have parse errors.
- Function-pointer and `std::function` calls can remain unresolved. Possible virtual-call targets indicate potential behavior rather than proof of a runtime path.
- C/C++ data writes are based on assignments and increment/decrement operations; a method call such as `x.reset()` may count as a read rather than a write.
- Ada tree-sitter links are best guesses and do not include data-access analysis. Use `libadalang` and the target's project context for richer results.
- AI checks validate claims against available graph facts; they do not establish complete behavioral correctness. Review flagged explanations and verify critical conclusions in the source.
- The web interface and demo are designed for local use. Source browsing requires the matching target checkout, and the guided tour expects the JSBSim sample.
