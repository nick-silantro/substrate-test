---
name: entity-query
description: Find and retrieve entities from a functional system. Use when user says "find", "list", "what's under", "show me all", "what depends on", "what belongs to", "get all work", or similar query/search operations. Handles relationship traversal, filtering by type, and dimensional status queries.
author: Nick Silhacek
version: 0.5.0
last_edited: 2026-03-04
---

# Entity Query

Find and retrieve entities using query.py and SQLite.

## find vs search — Know Which to Use

**`find`** — name-based LIKE query against the database. Use this when you know what the entity is called.
- "Find the Surface product" → `query.py find "Surface"`
- "Where is the RSW project?" → `query.py find "Real Substrate Work"`
- Fast, reliable, exact. If the name is known, always use `find`.

**`search`** — semantic/embedding-based. Use this when you want entities by *meaning*, not name.
- "What tickets relate to agent coordination?" → `query.py search "agent coordination"`
- "Find ideas about monetization" → `query.py search "monetization"`
- Slow, approximate. Good for discovery. Bad for name lookups — will return semantically-adjacent noise instead of the intended entity.

**The failure mode:** using `search` when you meant `find` returns confident-looking results that are semantically adjacent to your query but not the entity you wanted. It does not fail visibly. When in doubt, use `find` first.

## Primary Tool: query.py

```bash
substrate query COMMAND [ARGS]
```

| Command | Purpose | Example |
|---------|---------|---------|
| `find "term"` | **Name lookup** — use when you know what the entity is called | `query.py find "Surface"` |
| `search "query"` | **Semantic search** — use when you want entities by meaning, not name. Add `--format json` for machine-readable output. | `query.py search "trading intelligence"` |
| `entity UUID` | Full entity details | `query.py entity 550e8400` |
| `type TYPE` | All entities of a type | `query.py type task` |
| `pending` | Unresolved work items | `query.py pending` |
| `active` | Actively worked items | `query.py active` |
| `relationships UUID` | All relationships | `query.py relationships 550e84` |
| `children UUID` | Direct children (contains) | `query.py children 550e84` |
| `tree UUID` | Full hierarchy | `query.py tree 550e84` |
| `dim DIMENSION [VALUE]` | Query by dimension | `query.py dim focus active` |
| `workable` | Items available for work | `query.py workable` |
| `stuck` | Permanently blocked items | `query.py stuck` |
| `by ACTOR` | Work performed/owned by actor | `query.py by nick` |
| `unprocessed AGENT [TYPE]` | Not yet processed by agent | `query.py unprocessed carl` |
| `changelog` | Recent change history (CDC log) | `query.py changelog --last 10` |
| `due [N] [--type T]` | Overdue or due within N days | `query.py due 7 --type chore` |
| `chores [--due]` | All chores with status/streak | `query.py chores --due` |
| `triggers` | Active trigger registry | `query.py triggers` |
| `trigger-history [UUID]` | Recent trigger/cascade events | `query.py trigger-history` |
| `completion-history UUID` | Completion history for entity | `query.py completion-history UUID` |
| `stats` | Workspace overview | `query.py stats` |

UUID prefixes work — you don't need the full UUID.

## How Queries Work

Every query answers three questions:

1. **Starting point** — What entity am I starting from?
2. **Traversal** — Which relationships do I follow, and how far?
3. **Filter** — What subset of results do I return?

Natural language queries combine these. For example:

> "Show me all tasks under Project Alpha"

- Starting point: Project Alpha → `query.py find "Project Alpha"` to get UUID
- Traversal: Hierarchical, recursive downward → `query.py tree UUID`
- Filter: task type only → look at type column in output

## Starting Point

### By Name
> "Find the Q2 Campaign"

```bash
substrate query find "Q2 Campaign"
```

### By UUID
> "Get entity 550e8400..."

```bash
substrate query entity 550e8400
```

### By Context
> "What's under this?" / "Show me its dependencies"

Use the entity UUID from conversation context.

### No Starting Point (List All)
> "Show me all projects"

```bash
substrate query type project
```

## Traversal

### No Traversal
> "Find Project Alpha"

Just locate and return the entity.

### Immediate Relationships
> "What does this task belong to?"

```bash
substrate query relationships UUID
```

Shows both outgoing and incoming relationships.

### Recursive Hierarchy
> "What's under Project Alpha?"

```bash
substrate query tree UUID
```

Shows the full contains-hierarchy recursively.

### Direct Children Only
> "What does this project contain?"

```bash
substrate query children UUID
```

## Dimensional Status Queries

### By Dimension Value
> "Show me everything that's active"

```bash
substrate query dim focus active
```

### Dimension Distribution
> "What's the breakdown of focus states?"

```bash
substrate query dim focus
```

Returns counts per value (e.g., active: 15, idle: 30, blocked: 2).

### Common Dimensional Queries

| Question | Command |
|----------|---------|
| What's pending? | `query.py pending` |
| What am I actively working on? | `query.py active` |
| What's blocked? | `query.py dim focus blocked` |
| What's completed? | `query.py dim resolution completed` |
| What's high priority? | `query.py dim importance_tactical high` |
| What's in progress? | `query.py dim life_stage in_progress` |

## Actor & Agent Queries

### Work by Actor
> "What is Nick working on?" / "Show Carl's tasks"

```bash
substrate query by nick
substrate query by carl
```

Shows tasks performed by an actor (`performed_by` relationship) and work assigned to them (`assigned_to` relationship). Accepts UUID, short UUID prefix, or name (case-insensitive for user/agent entities).

### Unprocessed Entities
> "What hasn't Carl seen yet?" / "Show diary entries no agent has read"

```bash
substrate query unprocessed carl
substrate query unprocessed carl diary-entry
```

Shows entities where the given agent is not in the `processed_by` list. Optional type filter narrows results.

## Change History Queries

The CDC changelog tracks every entity mutation with old/new diffs, agent identity, and cascade events.

```bash
substrate query changelog                  # Last 20 changes
substrate query changelog UUID             # History of a specific entity
substrate query changelog --agent alpha    # Changes by a specific agent
substrate query changelog --since 2026-03  # Changes since date (ISO format)
substrate query changelog --op create      # Only create operations
substrate query changelog --last 50        # Last N entries
substrate query changelog --all            # All entries
```

Filters combine: `changelog UUID --op update --since 2026-03-01` shows only updates for a specific entity since March 1st. Backed by SQLite index for fast querying.

## Temporal & Recurrence Queries

### What's Due?

```bash
substrate query due                   # Overdue (next_due <= today)
substrate query due 7                 # Due within 7 days
substrate query due --type chore      # Only chores
```

### Chore Dashboard

```bash
substrate query chores                # All chores with streak, completion count, next_due
substrate query chores --due           # Only due/overdue chores
```

### Trigger Registry

```bash
substrate query triggers              # Built-in + entity triggers
```

Shows all 3 built-in triggers (completion_unblock, dependency_block, recurrence_reset) plus any entity triggers with their watches/acts_on relationships.

### Trigger & Completion History

```bash
substrate query trigger-history           # Last 20 cascade events
substrate query trigger-history UUID       # Cascades for specific entity
substrate query completion-history UUID    # Completions + resets with on-time tracking
```

`completion-history` shows each completion event, whether it was on time, and the recurrence reset details (new next_due, streak).

## Advanced: Direct SQLite

For queries that query.py doesn't cover, use SQLite directly:

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('_system/index/substrate.db')
c = conn.cursor()
c.execute('''
    SELECT e.name, e.type, e.focus, e.resolution
    FROM entities e
    JOIN relationships r ON e.id = r.source_id
    WHERE r.relationship = 'belongs_to' AND r.target_id LIKE '550e84%'
    AND e.type = 'task' AND e.meta_status = 'live'
''')
for row in c.fetchall():
    print(row)
"
```

### Useful SQLite Tables

- `entities` — id, name, type, description, path, meta_status, focus, life_stage, assessment, importance_tactical, resolution, health, importance_strategic, phase, processed_by, due, created, modified
- `relationships` — source_id, relationship, target_id
- `changelog` — rowid, timestamp, operation, entity_id, entity_type, entity_name, agent, triggered_by, raw_json (derived index; source of truth is JSONL files in `_system/logs/`)

## Language Signals

| User Says | Approach |
|-----------|----------|
| "find", "search", "where is" | `query.py find "term"` |
| "show", "details", "tell me about" | `query.py entity UUID` |
| "list all", "show me all [type]" | `query.py type TYPE` |
| "under", "beneath", "children" | `query.py tree UUID` or `query.py children UUID` |
| "belongs to", "contained by", "links" | `query.py relationships UUID` |
| "pending", "what needs work" | `query.py pending` |
| "active", "in progress" | `query.py active` |
| "blocked" | `query.py dim focus blocked` |
| "assigned to", "working on", "owned by" | `query.py by ACTOR` |
| "unprocessed", "hasn't seen", "not read" | `query.py unprocessed AGENT [TYPE]` |
| "history", "what changed", "changelog" | `query.py changelog [UUID]` |
| "search", "find by meaning", "semantic" | `query.py search "query text"` |
| "due", "overdue", "what's due" | `query.py due` or `query.py due 7` |
| "chores", "recurring tasks" | `query.py chores` |
| "triggers", "automations" | `query.py triggers` |
| "completion history", "streak" | `query.py completion-history UUID` |

## Output

Query results include UUIDs (or prefixes), names, types, and dimensional status. Hand off to entity-presentation for formatted display if the user needs a polished view.
