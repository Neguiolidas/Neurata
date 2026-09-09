---
name: neurata
description: Use when work touches knowledge worth keeping or worth recalling —
  recall before starting recurring topics, deposit when a decision or fact
  should survive the session, via the neurata.* MCP tools.
---

# Neurata (native integration)

Neurata is persistent grain memory: notes, decisions and facts stored as
grains that survive sessions, ranked by use. Two faces:

- **Manual:** the `/neurata:*` slash commands — query, deposit, expand, shelf.
- **Automatic (prefer this):** reach for the `neurata_*` MCP tools yourself
  when the moment calls for it — don't wait to be told.

## When to act automatically

- **Before starting work on a recurring subject** → `neurata_query` the topic
  first; prior decisions beat rediscovery.
- **A decision or fact is settled that will outlive this session** →
  `neurata_deposit` it, once, at the moment it is settled.
- **A query matched a grain but you need more of it** → `neurata_expand`
  (start at `summary`, go `full` only if needed).
- **The user asks what is stored / what is stale / what conflicts** →
  `neurata_shelf` (`inventory`, `insights` or `conflicts`).
- **A deposit came back `duplicate`** → do not deposit again; the existing
  grain is the record. Extend it only if its content is now wrong.

## When NOT to deposit

Session-scoped state (todo lists, partial progress, anything already tracked
in the repo). If it would be noise tomorrow, it is noise in memory.
