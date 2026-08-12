<p align="center">
  <!-- Absolute URL on purpose: PyPI renders this README outside the
       repository, where a relative path is a broken image at the top
       of the project page. -->
  <img src="https://raw.githubusercontent.com/Neguiolidas/Neurata/main/docs/assets/neurata-banner.jpg" alt="Neurata — the living memory of your environment: deposit raw, curate quietly, retrieve at the right moment" width="820">
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
pip install neurata      # or: pipx install neurata
```

From source, for hacking on it:

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
neurata query "term regime:curated"   # facet: what the archive owns
                                      # (regime:mirror = synced from a source)
neurata query "term agent:hermes"     # provenance: who deposited it
                                      # (agent:/session:/origin:, curated only)
neurata query "term missing:agent"    # the gaps: curated grains with no agent
neurata expand <id>          # card → summary → full

# archive health
neurata doctor
neurata --version
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

**Status:** v1.1.0. The 1.0 gate was dogfooding, not a version number:
`neurata doctor` measures it, and it cleared at 12 distinct days of real
use inside a 14-day window.

**License:** AGPL-3.0-or-later.
