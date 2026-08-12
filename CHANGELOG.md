# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 versions were an internal dogfooding cycle; spec milestones
v0.4–v0.7 shipped under the 0.8.0 release. None of the 0.x versions was
ever tagged or uploaded anywhere — they are development history, kept
for the record. For anyone installing the package, 1.0.0 is the history.

## [1.1.0] - 2026-08-12

Provenance becomes searchable: who deposited a grain, in which session,
from where — and which grains still answer none of that.

### Added
- Provenance facets `agent:`, `session:` and `origin:` —
  `neurata query "term agent:hermes"`. Provenance is read from each
  grain's frontmatter and stored in three real columns, so the facet
  filters instead of guessing.
- `missing:<facet>` — the inverse question: which curated grains still
  have no provenance (`neurata query "missing:agent"`). Valid keys are
  the three provenance facets; anything else (`missing:xpto`) and
  `missing:… regime:mirror` are usage errors with a message, not empty
  lists — `[]` there would claim "no gaps" where everything is a gap.

### Changed
- **Behaviour change:** `agent:`, `session:` and `origin:` used to fall
  through to free text, so `agent:hermes` matched any grain that merely
  mentioned either word. They now filter. Saved queries that relied on
  the old fan-out return fewer (and different) results.
- Provenance facets only ever match curated grains. A mirror carries the
  frontmatter of the source it reflects, not a depositor, so its three
  columns are NULL by construction — and `missing:` says so explicitly
  instead of drowning the real gaps in the whole mirror.
- Index schema v8: three provenance columns on `entries`. An index
  written by 1.0.0 migrates in place on the next command — `ALTER TABLE`
  plus a backfill that re-reads each curated grain's frontmatter, under
  the index lock and in one transaction, so a failure halfway leaves the
  index exactly as it was, still stamped v7. No reindex, no journal loss:
  23 ms over an index of ~15 k grains here.

### Fixed
- `query` on a `NEURATA_HOME` that had never been reindexed refused to
  search ("index missing or on an old schema — run `neurata reindex`")
  even when nothing was wrong. It was the first command a new user ran
  after `deposit`, and it was a wall. An unstamped index with `.md` files
  on disk is now rebuilt on the spot; an unstamped index with an empty
  disk returns zero results, which is the truth. A genuine schema
  mismatch still errors out: migrating is the user's decision, not the
  side effect of a search.

### Upgrade note
Point every consumer of the same `NEURATA_HOME` at 1.1.0 together. The
migration is one-way in practice: 1.0.0 does not know the new columns,
so any `reindex` it runs rebuilds the index at v7 and drops them, and
the next 1.1.0 command migrates it back. Nothing is lost — provenance is
re-derived from the files — but the round trip costs a full reindex
(43 s for ~15 k grains here) against 23 ms for the migration.

### Known limitations
- Facet values are matched literally, case included: `agent:hermes` and
  `agent:Hermes` are different values (36 results against 0 in the
  archive this was measured on).
- A facet value cannot contain spaces, and quoting does not help:
  `agent:"claude code"` drops the facet and searches for the text
  `agent:` instead. Both limitations predate 1.1.0 and apply to every
  facet, provenance or not.

## [1.0.0] - 2026-08-11

First published release.

### Added
- `neurata --version` (and `neurata --json --version`, which reports the
  version inside the standard envelope).
- `harvest` accepts arbitrary directories, not just the Claude Code
  skill layout: recursive generic provider (size, symlink, binary and
  permission guards) plus format adapters (`skill-md`, `markdown`,
  `yaml`, `rules`) auto-detected by suffix, with heuristic fallback —
  `neurata harvest <dir> [--target T] [--format F]`, where the source
  is either a named provider (`claude-code`) or a directory, and `F` is
  one of `auto`, `skill-md`, `markdown`, `yaml`, `rules`. Harvested
  items are namespaced `<target>@<hash of the resolved dir>` and keyed
  `<namespace>:<relpath>`, so equal basenames from different sources
  stop colliding. The harvested root may never contain `NEURATA_HOME`.
- `doctor`: `gate` check — the 1.0 dogfooding gate (10 real days of
  use inside a 14-day window).
- Two-regime library. Both regimes live in `library/`; what tells them
  apart is `source_key`, which only `tick` writes when it mirrors an
  external source. `regime` is derived from its presence (`mirror` when
  set, `curated` otherwise) and is never a field anyone authors, so the
  index and the files cannot disagree. The mirror is a re-syncable
  reflection of someone else's source; the curated side is what the
  archive owns. Search gained the facet `regime:` (`neurata query "term
  regime:curated"`), a dedicated `curated_fts` lane, and a guaranteed
  floor of curated results in the top-k footer —
  `regime.curated_quota` (default 3, capped at `limit // 2`) so a large
  mirror can never crowd the library out.
- `doctor`: `regime` check — fails on a mirrored grain marked as
  refined (curation the next `tick` would overwrite) and on a
  `curated_fts` lane out of sync with `entries`. 16 checks total.
- Tag-driven release workflow (test → version guard → build → smoke →
  publish over OIDC) and packaging metadata in `pyproject.toml`. The
  smoke job installs the built wheel on a runner that never checks the
  repository out, so `import neurata` can only resolve to the artifact:
  nothing reaches PyPI without having run as an installed package. A
  PyPI upload cannot be taken back or replaced, so this is the last
  place a broken build can still be stopped.
- CI: `wheel` job — builds, runs `twine check`, installs the wheel
  without `-e` and exercises the CLI outside the repository.

### Changed
- `INDEX_SCHEMA_VERSION` 6 → 7. The index rebuilds itself on the next
  operation; the files are untouched (the index is a disposable cache).
- `compact` is monotonic: it refuses to demote a grain that is already
  refined, so re-running the Miner over a compacted archive can no
  longer trade a summary back for a raw body.
- Trove classifier `Development Status` 4 - Beta → 5 -
  Production/Stable.

### Fixed
- Packaging: the wheel shipped without `neurata.providers` and
  `neurata.providers.formats`, because `[tool.setuptools]` listed
  `packages = ["neurata"]` and subpackages are not implied. Every
  command died on `ModuleNotFoundError` — `cli` imports `harvest`, which
  imports `providers` at module level — so an install from the artifact
  could not even print `--version`. The whole test suite stayed green
  throughout: CI installed with `pip install -e .`, which puts the
  source tree on `sys.path` and never consults that list. Replaced by
  automatic discovery (`[tool.setuptools.packages.find]`).
- Morphology was rewriting the whole query instead of expanding terms:
  `notas de datapipe` also ran as `nota de datapipe`, a reading that
  competed with the real one and pushed legitimate documents off the
  page. Each token now carries its own number variant.
- `doctor`: `index-freshness` warned falsely. It compared mtime against
  `last_reindex`, but only `reindex` stamps that field — a `tick`
  crossing a second boundary rewrote the library and looked stale. The
  verdict now comes from the content hash against the index, with mtime
  as a hint only.
- `deposit` rejects empty or whitespace-only input instead of filing a
  blank note.
- YAML adapter reads `info.name`/`info.description` on the regex path
  too. Without PyYAML installed an OpenAPI doc fell back to the
  filename, so the title changed depending on an optional dependency —
  the same file could enter the index twice.
- CI: the version guard job installs the package before importing
  `__version__`, which failed with `ModuleNotFoundError` on every tag
  push.
- Lint gates stated one thing and ran another: `severity`/`confidence`
  under `[tool.bandit]` are CLI flags, not config keys, so the gate
  silently ran at low/low; replaced by explicit skips of `B404`, `B603`
  and `B607` (`git` is called on purpose, always with an argv list) and
  case-by-case `nosec` on `B608`, with `shell=True` (`B602`) still on.
  `vulture` scanned only `neurata/`, so report counters incremented
  from the tests read as dead code (15 false positives); it now covers
  `tests` and exempts what is by definition never called by our code —
  pytest fixtures and the parameters monkeypatch stubs must accept.
- Tests: the `os.stat` stub delegates to the real stat and overrides
  only `st_dev` (`real.st_dev + 1`). It used to swap the whole module's
  `os.stat` for an object carrying just `st_dev`, so every stat in the
  process hit it and `pathlib` broke on 3.11/3.12 (`is_dir()` reads
  `st_mode`) — the test passed on 3.10 and 3.13 by accident.

## [0.9.0] - 2026-07-24
- Usage invocation log (`usage.log`, one line per CLI call) with
  best-effort readers — meters the 1.0 dogfooding gate.
- `doctor`: `last-tick` freshness check (warns when the hourly cron
  stops).
- Hardening: usage readers tolerate UTF-8 torn lines (never crash).

## [0.8.0] - 2026-07-23
- Tick pipeline inbox→library: near-dup detection, renames/orphans,
  `--budget`, JSON envelope.
- Harvest: environment scanning with `source_key` (Claude Code
  SKILL.md provider), tick integration.
- Snapshot: git-backed archive snapshots — commit on tick, atomic
  restore via read-tree, `snapshot` subcommand, optional auto-push.

## [0.3.0] - 2026-07-18
- Query pipeline: NFKD text normalization, router fan-out
  (OR/bag-of-words), RRF k=60, deterministic lexical ranking.
- CLI: `query`, `expand` (card → summary → full) with `--restore`,
  shelf score boost, latency bench; `doctor` grows to 10 checks.
- Foundations: TOML config, indexdb schema, usage event log.

## [0.1.0] - 2026-07-11
- Initial scaffold (renamed from Armarium): `deposit`, inbox layout,
  CI (lint + tests, Python 3.10–3.13).
