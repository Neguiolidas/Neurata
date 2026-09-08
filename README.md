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
neurata query "term agent:agente-exemplo"     # provenance: who deposited it
                                      # (agent:/session:/origin:/project:,
                                      #  curated only)
neurata query "term missing:agent"    # the gaps: curated grains with no agent
neurata query "term tag:osint"        # tags the SOURCE declared (v1.8 carries
                                      # them from yaml/skill/markdown into
                                      # the index — mirrors included)
neurata query "entity:postgres"       # every grain that names it: title,
                                      # alias, tag, project or source, in
                                      # one query (see Entity graph below)
neurata query "term --include-stale"  # tombstoned grains are excluded by
                                      # default; this flag brings them back
neurata expand <id>          # card → summary → full
neurata expand <id> --restore # bring the full body back from the archive

# shrink a grain's served body; the full is archived, never dropped
neurata compact <id>         # no-op unless it actually shrinks; refuses
                             # mirrors (the next tick would undo it)

# what the archive itself flags
neurata shelf --conflicts    # near-duplicates and id/slug collisions

# contradictions: "use X" vs "never use X" — same target, opposite
# polarity, detected deterministically at tick/reindex (no LLM)
neurata contradictions            # report: open pairs, resolved count
neurata contradictions --resolve  # apply the deterministic winner rule
neurata supersede <id> --by <id>  # or pick the winner yourself; the
                                  # loser is marked in its frontmatter
                                  # (`superseded_by`), never deleted
neurata query "term status:superseded"  # and search stops being blind:
                                        # cards carry `contradicts` and
                                        # superseded grains sink

# mirror an external source into the inbox (then `tick` catalogues it)
neurata harvest                        # default provider: claude-code skills
neurata harvest project                # THIS repo's instruction files:
                                       # AGENTS.md, CLAUDE.md, copilot-
                                       # instructions.md, .cursorrules and
                                       # .cursor/rules/*.mdc — found at the
                                       # git root of the cwd (override with
                                       # NEURATA_PROJECT_ROOT), each grain
                                       # classed by its shape
neurata harvest ~/some/dir             # or any directory, format auto-detected
neurata harvest ~/rules --format rules # or pinned: skill-md, markdown, mdc,
                                       # yaml, rules

# archive health
neurata doctor
neurata --version

# rebuild the whole index from the files (the files are the truth, so
# this is always safe): resolves every wikilink, refills the alias and
# entity tables, recomputes contradictions
neurata reindex
```

## Living links (wikilinks → graph)

`[[wikilinks]]` in a grain's body become graph edges — resolved by
slug, title or alias, deterministically, **at catalogue time**: every
`tick` writes the edges of the grain it is cataloguing (and an edit
that absorbs a body change rewrites them). Ambiguous targets (two
grains sharing a title or alias) resolve to nothing, by the same rule
the full reindex applies. Edges feed the Personalized PageRank leg of
the ranking — a grain linked from the top results rises, with no LLM
and no embedding. `neurata reindex` reconciles the whole graph (and
backfills the alias table) whenever you want a full pass.

## Entity graph (light)

Every grain declares the entities it is about — title, aliases, tags,
project, source — extracted deterministically (no NER, no LLM) into a
membership table. One facet searches across all the names:

```bash
neurata query "entity:postgres"   # grains that ARE, tag, or come from
                                  # the entity — title, alias, tag,
                                  # project and source in one query
```

Entities are also hubs in the graph leg of the ranking: grains that
share a name rise together even with no `[[link]]` between them. A hub
is built only for an entity naming between 2 and 64 grains — the band
where a shared name is a relation. A name carried by a single grain
would only hand that grain its own mass back, and a name carried by
half the archive is a category: it costs real time and drags in grains
that share no word with the query. The ceiling applies to the ranking
alone — `entity:` still answers for a source with thousands of mirrors.

Editing frontmatter (adding an alias) requires `neurata reindex` for
the membership to follow; body changes are absorbed by the tick as
usual.

## Context bias (deterministic)

The search knows where you are — the same way the deposit does since
v1.2. Grains from your current project, your current session, and
recently touched grains get a modest, fully deterministic boost before
the result is cut; no LLM, no embedding, no service.

```bash
neurata query "deploy"          # project from git root of the cwd
NEURATA_PROJECT=myrepo neurata query "deploy"   # explicit, zero cost
NEURATA_SESSION=$ID neurata query "deploy"      # session affinity
```

- `NEURATA_PROJECT` wins over the git probe (agents should set it);
  the git probe has a 0.5 s timeout and failure means "no bias", never
  an error.
- An explicit facet mutes its bias: `project:X` disables the project
  boost for that query — the facet is sovereign.
- Every JSON answer declares what it used: `"context": {"project",
  "session", "source"}` — a bias you cannot see is a bias you cannot
  trust. `source` is `env`, `git`, `none`, or `disabled` (bias switched
  off in config, so nothing was even looked at).
- All three knobs live in `config.json` under `"context"`
  (`project_boost`, `session_boost`, `recency_weight`,
  `recency_tau_dias`); zeroing one switches that hint off cleanly.
- The pre-cut recency hint and the shelf's recency are two different
  layers: the hint decides who *enters* the top-k, the shelf reorders
  *inside* it.

## Automatic curation

`tick` catalogues whatever is in the inbox. Run it hourly via cron:

```cron
0 * * * * $HOME/.local/bin/neurata tick >> $HOME/.neurata/logs/cron-tick.log 2>&1
```

`neurata doctor` warns (`last-tick`) if the cron stops.

`tick` also absorbs edits: fix a grain's **body** in your editor and the
next tick re-hashes it into the index, keeping its id, slug and
provenance. Editing only the frontmatter (`class:`, `type:`, `tags:`,
`aliases:`) leaves the body unchanged, so the tick sees nothing to
absorb — run `neurata reindex` after those.

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

**Status:** v1.11.0, in daily use on a real vault. `neurata doctor`
reports index health at any moment. Snapshot previews are read-only, managed
libraries use an isolated local Git identity, and stale grains are excluded
from graph propagation unless `--include-stale` is explicit.

**License:** AGPL-3.0-or-later.
