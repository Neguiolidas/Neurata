# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 versions were an internal dogfooding cycle; spec milestones
v0.4–v0.7 shipped under the 0.8.0 release. None of the 0.x versions was
ever tagged or uploaded anywhere — they are development history, kept
for the record. For anyone installing the package, 1.0.0 is the history.

## [1.4.0] - 2026-08-15

Compacting a grain used to mean losing track of what it originally said.
1.4 splits the two bodies an archive actually has — the one it *serves*
and the one it was *given* — so shrinking a grain is a change of
representation, never a change of content.

### Added
- **`derived_hash` vs `content_hash`**: the index now hashes the served
  body separately from the deposited one. A compacted grain has two
  different hashes by construction, and every check that used to compare
  them (tick reconciliation, doctor) knows which of the two it means.
  The full body always comes back byte-for-byte from the archive.
- `doctor` gains a **`derived-integrity`** check: it fails when a grain
  says it was derived from an archived body that is no longer there.
  That is the one loss in this area that no reindex can undo — the
  served body is a summary and the original is gone — so it reports as
  `fail`, not as a warning to be scrolled past.

### Changed
- Index schema v10 → v12, migrated in place. v11 opens `derived_hash`
  and `derived_from`; v12 **packs `shingles`** from a JSON array of hex
  strings into a blob of 8-byte keys. Measured on this archive: 82.8 MiB
  → 33.1 MiB, **−60%** over 14,979 grains and 4.34 M shingles, with the
  same near-duplicate results — it is the same set, stored as bytes
  instead of as text about bytes.
- `compact` is **monotonic in size**: it only writes when the summary
  actually shrinks the body. A body of consecutive headings used to come
  back *larger* than it went in (blocks rejoined with a blank line), so
  "compacting" grew the served corpus; that case is now a no-op with a
  reason.
- `compact` no longer stamps `updated`. Changing how a grain is stored
  is not the grain saying something new, and `shelf` scores recency:
  stamping it would have zeroed the age of the whole archive at once and
  floated freshly compacted grains over intact, curated material.
- `compact` writes in crash-safe order (archive → file → index) and, if
  the index is locked by another process, returns
  `compacted-pending-index` instead of reporting failure for work that is
  already on disk. The index catches up on the next `tick` or `reindex`.
- `compact` refuses mirrors (`regime='mirror'`) and `refined` grains.
  Both are work that evaporates without warning: the next `tick` rewrites
  a mirror's body from its source, dropping the derivation and orphaning
  the archived blob, and the mechanical Miner does not demote what the
  DeepMiner refined.
- `tick` runs `git gc --auto` after its commit. Each commit leaves loose
  objects behind, and a batch of compactions touches hundreds — measured
  at +1.69 MiB of loose objects against −1.65 MiB of body over 1,004
  grains, i.e. the audit trail growing faster than the archive shrank.

## [1.3.0] - 2026-08-13

An archive that only knows *where* a grain came from cannot tell a dated
event from a durable fact. 1.3 adds the axis it was missing — and it is
declared in the file, never guessed from the text.

### Added
- **Memory axis**: every grain carries a `class:` — `episodic` (a dated
  event), `semantic` (a fact about the world) or `procedural` (how to do
  something). Precedence is **explicit declaration > regime default**,
  derived in one place for every write path (`tick` and `reindex`), so
  the index cannot invent a class the file does not have.
- A curated grain with nothing declared is `episodic`, anchored on
  `created`, which every deposit has: a deposit is a dated event, and a
  dated event is episodic memory. That is form, not text heuristics.
- Mirrors carry the `class:` the harvest wrote, because the adapter is
  what knows which shape it read. A skill is `procedural` because it was
  read as a skill — auditable by opening the file, not knowledge hidden
  in the code.
- `class:` is a query facet, with `missing:class` for the gaps:
  `neurata query "deploy class:procedural"`. Its domain is closed, so a
  typo is a usage error listing the valid classes, never an empty list:
  `class:procedual` returning nothing would read as "I have no
  procedural memory" when what does not exist is the word.
- `shelf --conflicts` now also lists near-duplicates found by `tick`
  (`conflicts_with`), not only `id`/`slug` collisions.

### Changed
- Index schema v9 → v10 opens three columns (`class`, `source_path`,
  `derived_hash`) and runs **zero `UPDATE`**: they are born `NULL` and
  filled by re-derivation (harvest → tick → reindex). Reading 15 k files
  inside a migration would be a second implementation of the derivation,
  free to drift from the first. Measured here at 45–64 ms over 14,979
  grains (three runs).
- `edges` is **re-keyed** from `rowid` to `id`, with its contents
  translated by a `JOIN` inside the bank — never dropped. The `rowid` was
  unsafe because the tick's update-in-place deletes and reinserts: SQLite
  recycles the number and the edge would silently point at whichever
  grain inherited it. Dropping instead of translating would have left the
  graph signal out of `query`'s ranking until the next reindex, in
  silence. On a synthetic 60 k-edge index the translation costs 1.2 s and
  preserves all 59,991 edges; the archive here has an empty `edges`, so
  its own migration pays nothing for this.
- A grain no longer conflicts with itself. An item already indexed in the
  inbox escaped the exact-dedup (which only looks at `location='library'`)
  and came back as a near-dup against itself at jaccard 1.0. The tick now
  skips it in the loop **and** heals the self-reference already written to
  disk; `shelf --conflicts` filters it out for grains written by < 1.3.

### Fixed
- `class:` was never written to the index. Both write paths declared the
  column and neither passed the value, so the facet answered nothing while
  the files carried the datum. Caught by a real smoke, not by the suite —
  the tests asserted the derivation, and the derivation was right.

## [1.2.0] - 2026-08-13

The provenance facets of 1.1.0 had no data to filter: nothing ever wrote
who deposited a grain. Now the deposit reads it from the environment, and
a grain edited by hand stops being invisible to the archive.

### Added
- Provenance is captured automatically on `deposit`. `agent` and
  `session` come from the environment — `NEURATA_AGENT` / `NEURATA_SESSION`
  first, then the host's `AI_AGENT` / `CLAUDE_CODE_SESSION_ID` — and an
  explicit argument still wins over both. Absent stays `None`, never an
  empty string: `missing:agent` has to keep meaning one thing.
- Agent names are normalised: `claude-code_2-1-229_agent` becomes
  `claude-code`. Without it every host upgrade would invent a new agent
  and shatter the `agent:` facet. The rule drops a trailing `agent`
  segment and any version-shaped segment; if that empties the name, the
  raw value is kept — normalising must never erase the datum.
- `project:` now has data. It is derived in the index from the basename
  of the grain's `source.git_root`, so deposits made inside a repo answer
  `neurata query "term project:Neurata"`. An explicit `project:` in the
  frontmatter wins; mirrors are `None` by construction.
- `tick` absorbs manual edits. Editing an indexed `.md` used to be
  invisible — the preflight only ever read files the index did not know,
  so the archive served the old body forever. The new step re-hashes each
  library grain, and on a mismatch reindexes it in place, keeping `id`,
  `slug`, `path`, `created` and every `source.*` field, and clearing the
  stale mark. It only absorbs when the file's `meta.id` matches the
  indexed one: a different id is not an edit, it is a swapped identity,
  and that still goes to quarantine. Unreadable or broken frontmatter is
  skipped in silence — judging integrity is `doctor`'s job.

### Changed
- `CONTRACT_VERSION` 3 → 4: the tick envelope gained `absorbed`, and the
  snapshot body gained an `absorve:` line (7 fixed lines, was 6).
- Index schema v9 backfills `project` for curated grains from their
  frontmatter, under the index lock and in one transaction: 20–191 ms
  over an index of ~15 k grains (112 curated) here, filling 79 of them —
  the ones that carry a `source.git_root`. Mirrors are excluded by the
  query itself, not by luck.
- Unlike the v7→v8 backfill, v9 **skips** a grain whose file is missing
  or unparseable instead of writing `NULL`. v9 is enrichment, not
  reconstruction; blanking an existing value over a transient read error
  would be a loss.
- Migration now chains steps. An index still at v7 (written by 1.0.0)
  used to advance one step and stop with "run `neurata reindex`"; it now
  walks v7 → v8 → v9 in a single command, with a strict progress
  invariant — a step that fails to advance the stamp raises instead of
  looping forever under the index lock.

### Fixed
- A list in the frontmatter (`project: [a, b]`) crashed the tick's insert
  with `InterfaceError`. Deriving the value through one function, beside
  `provenance()`, applies the same defence: no type coercion, empty
  collapses to `None`.
- Fewer spurious quarantines. A grain that was both orphaned and edited
  failed the hash check of the orphan step and was purged + quarantined.
  Absorbing first syncs the hash, so that step now decides on identity
  alone, which is what it knows how to judge.

### Upgrade note
Same rule as 1.1.0: point every consumer of a `NEURATA_HOME` at 1.2.0
together. Measured here on a shared archive: 1.1.0 refuses to `query` or
`tick` a v9 index ("run `neurata reindex`"), and if you do run its
`reindex`, it rebuilds the index at v8 and drops `project`. Nothing is
lost — the next 1.2.0 command migrates back to v9 and re-derives the
column from the files — but the round trip costs a full reindex against
milliseconds for the migration.

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
