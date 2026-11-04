# Credential audit

Reviewed on November 4, 2026, before preparing this repository for GitHub.

## Findings

- A credential-shaped OpenAI-compatible API key is present in the local `.env`
  file at line 1. The file is ignored, untracked, and absent from the inspected
  Git history. Its value is intentionally omitted from this report.
- No confirmed credentials were found in the inspected source, configuration,
  documentation, generated data, or historical Git blobs. An exact-value search
  for the local key found no additional copies in the inspected content.
- Credential validity was not tested against an external service.

## Scope

- The existing repository's 11 commits reachable from local refs: 293 unique
  historical blobs, totaling approximately 205 MB. This included tracked JSON
  datasets and SQLite databases.
- The surrounding hackathon workspace: 383 files, totaling approximately 297 MB,
  excluding dependencies, downloaded external source repositories, Git internals,
  and media. This included first-party source/configuration/documentation,
  ignored environment files, generated graphs/caches/databases, and local
  assistant settings.
- 680 decompressed XML/text members from the workspace's Office presentations.
- Provider token patterns, private-key headers, and exact matching against the
  local API key were checked across the above content. An additional generic
  credential-assignment scan covered 147 first-party text files; SQL tokenizer
  settings were reviewed as false positives.

These are local, pattern-based checks, not a guarantee that every possible
secret format or prior remote exposure has been detected. Dependency trees,
external source repositories, and image/video/audio contents were not audited.

## Repository changes

- Extended `.gitignore` to exclude environment-file variants, private keys,
  credential files, local settings, generated data, downloaded repositories,
  build output, and browser test artifacts.
- Added `.env.example` with placeholders and offline mode enabled.
- Excluded environment files and private credentials from both Docker build
  contexts.
- Removed 115 generated data files and four external-repository links from the
  Git index, preserving all 119 local files/directories. Kept `data/.gitkeep`
  and `repos/.gitkeep` so clean checkouts retain the expected layout.
- Preserved the existing branch, remote, and commit history. Generated data
  remains in older commits; history was not rewritten.

## Verification

Git ignore checks confirmed that 17 representative sensitive/generated paths
are excluded, while seven required source/template/lockfile paths are included.
The index was checked to ensure `.env`, generated datasets, and external
repository links are excluded. C/C++, Ada, explanation, and store CLI help
commands were checked using the existing Python environment.

The application source was not changed; the full application test suite was
not run for this documentation and repository-hygiene update. No GitHub push
was performed.
