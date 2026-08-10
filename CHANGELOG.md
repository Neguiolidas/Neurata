# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Pre-1.0 versions are an internal dogfooding cycle (private repo);
spec milestones v0.4–v0.7 shipped under the 0.8.0 release.

## [Unreleased]
- Fix: YAML adapter reads `info.name`/`info.description` on the regex
  path too. Without PyYAML installed an OpenAPI doc fell back to the
  filename, so the title changed depending on an optional dependency —
  the same file could enter the index twice.

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
