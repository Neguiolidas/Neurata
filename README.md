<p align="center">
  <img src="docs/assets/neurata-banner.jpg" alt="Neurata — the living memory of your environment: deposit raw, curate quietly, retrieve at the right moment" width="820">
</p>

<p align="center">
  <b>Deposit raw, curate quietly, retrieve at the right moment.</b>
</p>

<p align="center">
  <a href="https://github.com/Neguiolidas/Neurata/actions/workflows/ci.yml"><img src="https://github.com/Neguiolidas/Neurata/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

The living knowledge layer for an agent's **environment** (CLI, IDE,
framework). Companion to [Conscio](https://github.com/Neguiolidas/Conscio),
which minds the agent itself; Neurata minds the agent's world: skills,
tools, docs, decisions — catalogued, curated, retrievable.

## Install

```bash
pip install neurata
```

*(Once published to PyPI — until then, install editable from a dedicated
venv:)*

```bash
git clone https://github.com/Neguiolidas/Neurata
python3 -m venv ~/.venvs/neurata
~/.venvs/neurata/bin/pip install -e ./Neurata
ln -sf ~/.venvs/neurata/bin/neurata ~/.local/bin/neurata   # if ~/.local/bin is on PATH
```

Zero runtime deps. The archive lives in `~/.neurata` (override with
`NEURATA_HOME`). `neu` is installed alongside `neurata` as a typing
convenience — docs always use `neurata`.

## Use

```bash
# deposit (stdin via '-', or positional text)
echo "raw content" | neurata deposit -
neurata deposit "raw content" --title "Note"

# catalogue the inbox (mechanical, reversible — nothing is destroyed)
neurata tick

# query (deterministic lexical)
neurata query "term"
neurata expand <id>          # card → summary → full

# archive health
neurata doctor
```

## Automatic curation

`tick` catalogues whatever is in the inbox. Run it hourly via cron:

```cron
0 * * * * $HOME/.local/bin/neurata tick >> $HOME/.neurata/logs/cron-tick.log 2>&1
```

`neurata doctor` warns (`last-tick`) if the cron stops.

**Principles**

- Files are the truth (a valid Obsidian vault); the index is a disposable cache.
- Deterministic retrieval first; LLMs only where explicitly wanted.
- Nothing is ever destroyed: archive + quarantine, never delete.
- Zero runtime dependencies. Python ≥ 3.10.

**Status:** v0.9 — the 1.0 dogfooding gate is cleared (`neurata doctor`
checks it). Not yet on PyPI.

**License:** AGPL-3.0-or-later.
