# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 versions were an internal dogfooding cycle; spec milestones
v0.4–v0.7 shipped under the 0.8.0 release.

## [Unreleased]

### Added
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
- Tag-driven release workflow (test → version guard → build → publish
  over OIDC) and packaging metadata in `pyproject.toml`.

### Changed
- `INDEX_SCHEMA_VERSION` 6 → 7. The index rebuilds itself on the next
  operation; the files are untouched (the index is a disposable cache).
- `compact` is monotonic: it refuses to demote a grain that is already
  refined, so re-running the Miner over a compacted archive can no
  longer trade a summary back for a raw body.

### Fixed
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
