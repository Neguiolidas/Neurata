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
# agent, session and project come from the environment and from the repo
# you deposit in — nothing to pass by hand

# catalogue the inbox (mechanical, reversible — nothing is destroyed)
neurata tick

# query (deterministic lexical)
neurata query "term"
neurata query "term regime:curated"   # facet: what the archive owns
                                      # (regime:mirror = synced from a source)
neurata query "term class:procedural" # memory axis: how-to, as opposed to
                                      # class:episodic (a dated event) and
                                      # class:semantic (a fact)
neurata query "term agent:hermes"     # provenance: who deposited it
                                      # (agent:/session:/origin:/project:,
                                      #  curated only)
neurata query "term missing:agent"    # the gaps: curated grains with no agent
neurata expand <id>          # card → summary → full
neurata expand <id> --restore # bring the full body back from the archive

# shrink a grain's served body; the full is archived, never dropped
neurata compact <id>         # no-op unless it actually shrinks; refuses
                             # mirrors (the next tick would undo it)

# what the archive itself flags
neurata shelf --conflicts    # near-duplicates and id/slug collisions

# mirror an external source into the inbox (then `tick` catalogues it)
neurata harvest                        # default provider: claude-code skills
neurata harvest ~/some/dir             # or any directory, format auto-detected
neurata harvest ~/rules --format rules # or pinned: skill-md, markdown, yaml, rules

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

`tick` also absorbs edits: fix a grain's **body** in your editor and the
next tick re-hashes it into the index, keeping its id, slug and
provenance. Editing only the frontmatter (`class:`, `type:`, `tags:`)
leaves the body unchanged, so the tick sees nothing to absorb — run
`neurata reindex` after those.

**Principles**

- Grains sit on two independent axes. **Regime** — who owns the grain —
  is derived from the file's shape, never authored: a grain carrying a
  `source_key` is a `mirror` of some external source, everything else is
  `curated`. **Class** — what kind of memory it is — is declared in the
  frontmatter (`class: episodic | semantic | procedural`); a curated
  grain without one is `episodic`, because a deposit is a dated event.
  A mirror's class is written by the harvest from the file's shape — a
  skill or a rules file is `procedural`, prose and YAML are `semantic` —
  and a shape the adapter doesn't know declares nothing at all. Neither
  axis is ever guessed from the text, and `neurata query "missing:class"`
  lists the grains that predate the declaration.

- Files are the truth (a valid Obsidian vault); the index is a disposable cache.
- Deterministic retrieval first; LLMs only where explicitly wanted.
- Nothing is ever destroyed: archive + quarantine, never delete.
- Zero runtime dependencies. Python ≥ 3.10.

**Status:** v1.4.0. The 1.0 gate was dogfooding, not a version number:
`neurata doctor` measures it, and it cleared at 12 distinct days of real
use inside a 14-day window.

**License:** AGPL-3.0-or-later.
