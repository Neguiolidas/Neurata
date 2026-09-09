# Neurata plugin for Claude Code

Persistent grain memory for Claude Code: deposit knowledge once, recall it in
every session after. This plugin wires the [Neurata](https://github.com/Neguiolidas/Neurata)
MCP server and its slash commands into your Claude Code setup.

## Requirements

- Claude Code with plugin support
- [`uv`](https://docs.astral.sh/uv/) on PATH (`uvx` runs the server, no
  separate install of Neurata needed)

## Install

From a checkout of this repository (or any marketplace that lists it):

```
claude plugin marketplace add <path-or-owner>/Neurata
claude plugin install neurata@neurata
```

## What you get

| piece | what it does |
|---|---|
| MCP server (`neurata`) | four tools: `neurata_query`, `neurata_deposit`, `neurata_expand`, `neurata_shelf` |
| `/neurata:query <q>` | search memory for prior knowledge |
| `/neurata:deposit <text>` | store a decision/fact so it survives sessions |
| `/neurata:expand <ref>` | open a grain at card/summary/full |
| `/neurata:shelf [view]` | inventory, usage insights, or conflicts |
| `neurata` skill | teaches Claude when to recall and when to deposit automatically |

## Where memory lives

The server resolves the memory home from the `NEURATA_HOME` environment
variable, or falls back to the Neurata default. Set `NEURATA_HOME` before
starting Claude Code to point the plugin at a specific memory.

## License

AGPL-3.0-or-later. Same license as Neurata itself.
