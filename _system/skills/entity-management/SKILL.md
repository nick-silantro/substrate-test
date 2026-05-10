---
name: entity-management
description: Create, update, and manage entities in a functional system. Use when user says "create task", "create ticket", "create meeting", "new entity", "update task", "rename entity", "mark as complete", "delete entity", or similar entity operations. Scripts handle UUID generation, folder structure, meta.yaml, SQLite indexing, and bidirectional relationships.
author: Nick Silhacek
version: 0.8.0
last_edited: 2026-05-10
---

# Entity Management

Create, update, and manage entities using the Substrate scripts.

## Critical Rules

1. **Always use scripts** — never manually create entity folders, write meta.yaml, or edit SQLite. The scripts handle UUID generation, sharding, meta.yaml creation, dimensional status defaults, SQLite indexing, and bidirectional relationships automatically.
2. **meta.yaml = pure YAML** — no frontmatter delimiters (`---`), no prose content. Just YAML fields.
3. **Schema compliance** — types, attributes, and relationships must come from the schema files (`_system/schema/*.yaml`). If a user requests something not in the schema, confirm before proceeding.
4. **Relationships = UUIDs** — never wiki-links, names, or paths.

## Creating an Entity

### 1. Know the Type

Read `_system/schema/types.yaml` for the full list. Key distinctions:

- **task** — an optional sub-unit created by an executing L2 agent at its own discretion, for internal tracking or posterity. Tasks always `belongs_to` a ticket. Not created by L1s; not the primary claiming surface — that's the ticket.
- **ticket** — the primary work unit and L2 claiming surface. One coherent output, one agent, one session. Sized so that the result is a single reviewable deliverable.
- **project** — a goal-oriented container for tickets, documents, decisions, and context.
- **chore** — a standalone personal task, typically recurring. First work type that doesn't require a parent ticket. Can optionally belong_to a workstream.
- **friction** — a pain point, bug, or inefficiency observed in a system or process.
- **document** — a file-based entity (the actual file lives alongside meta.yaml in the folder).
- **note** — a quick thought or capture that doesn't warrant a more specific type.
- **milestone** — a significant checkpoint on the path toward a horizon, with its own lifecycle and strategic weight. Use when the checkpoint itself matters beyond the tickets that deliver it — it can be tracked, reported on, and referenced by future work. Milestones fit missions, goals, and projects; they don't fit pillars (which are permanent and have no checkpoints). Distinct from **theme**: a milestone is a structured entity; a theme is a lightweight string label on work items for grouping tickets into planning batches.

Check the type's **grouping** in types.yaml — the grouping's **nature** determines which status dimensions apply (see Dimensional Status below).

**No retyping** — entities don't change type through lifecycle. If a task produces a project, create a new project entity and link them.

### 2. Check Required Relationships

Read `_system/schema/relationships.yaml` → `required_relationships` section.

Work entities ladder up through the hierarchy via `belongs_to`:
- task `belongs_to` ticket
- ticket `belongs_to` project/workstream/incident
- project `belongs_to` initiative
- project `serves` mission (crosses the work/object boundary — purposeful contribution, not structural hierarchy)
- mission `serves` pillar (same reason)

If a required relationship wasn't specified, ask the user which parent to link to. When creating a project, always ask which mission it serves — use `--serves MISSION_UUID`, not `--belongs_to`.

If the user explicitly declines a required relationship, pass `--attr orphan-ok=true`.

**Purpose-over-type:** Work belongs to its purpose, not its type. An article written for a project is a project deliverable — it belongs to the project, not to an article-production pipeline. When in doubt about where something belongs, ask: what is this in service of? That's the parent.

### 3. Write a Good Description

Every entity needs a description — a concise (1-2 sentence) explanation of purpose and relevance.

- Focus on *why this matters*, not restating the name
- Use available context: conversation, source material, related entities, user intent
- If context is limited: `"[awaiting context]"` (the script uses this as default)

### 4. Run create-entity.py

```bash
substrate entity create \
  --type TYPE \
  --name "Name" \
  --description "Description" \
  --belongs_to PARENT_UUID \
  [dimensional flags] \
  [other options]
```

The script handles everything: UUID generation, sharded folder creation, meta.yaml writing, SQLite indexing, and inverse relationship writing on the target entity.

**Common options:**

| Flag | Purpose |
|------|---------|
| `--type TYPE` | Entity type (required) |
| `--name "Name"` | Entity name (required) |
| `--description "Desc"` | Description (default: "[awaiting context]") |
| `--id UUID` | Use a specific UUID (default: auto-generate) |
| `--belongs_to UUID` | Hierarchical parent |
| `--relates_to UUID` | General association |
| `--comes_from UUID` | Origin context |
| `--RELATIONSHIP UUID` | Any relationship defined in the schema |
| `--due DATE` | Due date |
| `--every SHORTHAND` | Recurrence shorthand (see Recurring Entities below) |
| `--date-basis MODE` | Override next_date_basis: `scheduled` or `completion` |
| `--next-due DATE` | Override calculated initial next_due |
| `--attr KEY=VALUE` | Extra type-specific attribute (repeatable) |
| `--dry-run` | Validate inputs + preview what would be created |

Run with `--help` for all options including dimensional flags.

**Validation:** Both `create-entity.py` and `update-entity.py` validate inputs against the schema before executing. Invalid dimension values, forbidden fields, bad enum values, and connection rule violations are blocked with clear error messages. Warnings (missing required relationships, disallowed dimensions, undefined fields) print but don't block.

To validate without executing, use the standalone pre-checker:
```bash
python3 _system/scripts/precheck.py create --type task --name "..." --belongs_to UUID
python3 _system/scripts/precheck.py update UUID --focus active
```
Same flags as the entity scripts. Exits 0 (valid) or 1 (invalid). Useful for agents doing dry-runs and for Surface form validation.

### 5. Add Content (if applicable)

Scripts handle plumbing; content is your job. After creation:

- For document/script entities: place the actual file in the entity folder alongside meta.yaml
- For tickets/tasks: optionally write a `content.md` in the entity folder with goals, scope, or context
- Content files use headers, bullet lists, and prose. No markdown tables unless explicitly asked.

#### overview.md Convention (object entities)

Object-nature entities that accumulate meaningful state over time (products, pillars, missions, goals, milestones, persons, organizations, agents, libraries, and others) should have an `overview.md` in their entity folder. This is a **living document** — it reflects the current state of the object, not the history of how it got there.

`overview.md` covers: what this entity is (beyond the meta.yaml description), current state (architecture, key design decisions, settled tradeoffs), and ongoing principles that govern future work on it.

Maintenance: updated by the agent or human completing work that materially changes the object, as a side effect of that work — not a separate ticket. See the Object Documentation Pattern context-doc for the full convention.

Distinction: `overview.md` is cumulative ("what is this thing now?"); build-era doctrine in project entity folders is episodic ("what were we trying to do in that project?"). Both are needed; neither replaces the other.

#### decision.md Convention (decision entities)

Decision entities capture the deliberative record — what was weighed and why, not just what was concluded. Every decision entity should have a `decision.md` content file.

**Required sections:**
- **What Was Decided** — one to three sentences; precise enough to implement consistently
- **Alternatives Considered** — each alternative seriously weighed, and why rejected or deferred
- **Reasoning** — why the chosen path; should be falsifiable; a reader should be able to disagree with a specific claim
- **Confidence and Known Uncertainties** — confidence level (high/medium/low); what assumptions this rests on

**Strongly recommended:**
- **Questions Not Asked** — acknowledged gaps where the deliberation was incomplete
- **Conditions for Revisit** — specific triggers that would make this decision worth reopening

A decision's operative status is tracked via `phase`: `forming` (open, still being weighed) → `established` (settled and binding) → `aging` (in effect but under pressure) → `retired` (overridden, reversed, or no longer relevant). When a decision is superseded by another, link it with `supersedes` → `superseded_by`. Retirement has other valid causes too — the supervised entity no longer exists, the situation changed, assumptions turned out wrong.

**`system` attribute:** Set `system: true` when a decision affects how Substrate itself works — its schema, scripts, relationships, operating model, or agent behaviors. Enables flat queries (`WHERE system = true`) without graph traversal. A decision can be `system: true` and still `belongs_to` a user-domain project for cross-cutting decisions.

Graph connections: `governs` → entities this decision constrains; `governed_by` → decisions or principles that shaped this one; `decided_by` → person, agent, or diary-entry; `supersedes` → earlier decision this one replaced.

Full content template and design rationale: see the Decision Record Structure context-doc.

#### brief.md Convention

Project-grouping entities (project, workstream, initiative, incident) should have a `brief.md` in their entity folder. This is the orientation document for the work — short, real, and written before work starts.

**Required sections (all briefs):**
- **Purpose** — why this exists; 1-3 sentences; not a mission statement, an orientation
- **Endstate** — what winning looks like, concrete enough to evaluate at completion
- **Scope** — key deliverables (what's in); at least one explicit exclusion (what's out)
- **Constraints** — timeline, budget, dependencies; real ones that shape decisions; skip if nothing material
- **Notes** — decisions, context, and anything that doesn't fit above

**Outward-facing work adds:** (the single question: is this going to a public audience?)
- **Audience** — who this is for; shapes every creative decision downstream
- **Tone** — a few adjectives describing the attitude of the work
- **Channels** — where this lives; skip if self-evident
- **Success metrics** — 1-3 measurable indicators (strongly preferred)

**Optional for any complex work:**
- **Success metrics** — useful for long-running or high-stakes internal work too
- **Stakeholders** — owner (`assigned_to` relationship), contributors (`performed_by`), reviewer (`reviewed_by`); these are relationships, not meta.yaml fields

The outward-facing determination is almost always clear from the entity name and description. The `produces` relationship is a forward signal: if deliverables are declared at brief time, outward-facing is implied. For genuinely ambiguous cases, ask.

**Format:** headers and bullets; no tables; 1-2 pages max. The scope exclusion is the most discipline-forcing element — committing to what's not in scope prevents drift more than any other convention.

**Reference example:** `entities/project/fe/23/fe23db4a-1948-4a00-a9ff-1eac2a929c6e/brief.md` (Substrate Publicity project).

**Precheck:** When a life_stage update is applied to a project-grouping entity with no `brief.md`, precheck issues an advisory warning. This is never a hard block — it's a reminder.

## Dimensional Status

Status is dimensional — 8 independent dimensions, each answering a different question. Types pick which dimensions they use based on their grouping's nature (the "buffet model").

### The Dimensions

**FLAIR** (work-nature types — actions and efforts groupings: task, ticket, chore, project, workstream, initiative, incident):
- **focus** — Am I working on this right now? (idle, active, waiting, blocked, paused, closed)
- **life_stage** — Where in the workflow? (backlog, ready, in_progress, under_review, done_working)
- **assessment** — How is it going? (not_assessed, on_track, at_risk, off_track / exceeded, succeeded, mixed, failed)
- **importance_tactical** — How urgent? (critical, high, medium, low)
- **resolution** — Is this done? (unresolved, completed, cancelled, deferred, superseded)

**HIP** (object-nature types — all other groupings: horizons, planning, utility, knowledge, actors, artifacts, etc.):
- **health** — How is this doing? (growing, stable, declining, problematic, undefined)
- **importance_strategic** — How central? (core, important, peripheral)
- **phase** — Lifecycle stage? (forming, established, aging, retired)

### Defaults and Overrides

The script auto-populates dimensional defaults based on the type's nature. You only need to specify overrides.

**When to override defaults:**
- Setting a task to active/in_progress when it's being worked on immediately
- Setting importance_tactical to high or critical for urgent items
- Most creation uses defaults — override only when the default doesn't fit

**Dimensional flags on create-entity.py:**
```
--focus VALUE
--life-stage VALUE
--resolution VALUE
--assessment VALUE
--importance-tactical VALUE
--health VALUE
--importance-strategic VALUE
--phase VALUE
```

Disallowed dimensions for a type generate a warning and are ignored.

### Per-Type Exclusions

Most types use their nature's defaults. Exceptions are declared via `forbidden.types` on individual dimensions in `attributes.yaml`. For example, pillar has `resolution: forbidden` (pillars are permanent; they never resolve). Check each dimension's access declaration for the current list.

## Creating Chores (Recurring Entities)

Chores are standalone work entities that typically recur. Any entity can have recurrence, but chores are the primary use case.

### Quick Creation with --every

```bash
# Every 2 days
substrate entity create --type chore --name "Clean cat litter" --every 2d

# Mon/Wed/Fri
substrate entity create --type chore --name "Exercise" --every MWF

# 1st of every month
substrate entity create --type chore --name "Pay rent" --every 1st --date-basis scheduled

# Last day of month
substrate entity create --type chore --name "Monthly review" --every last
```

**Day abbreviations:** M=Mon, T=Tue, W=Wed, F=Fri, S=Sat. Use two-char for Thu/Sat/Sun: Th, Sa, Su.

**--every formats:**
- `Nd` — interval (e.g., `2d`, `7d`, `14d`)
- Day abbreviations — day_of_week (e.g., `MWF`, `TuThSa`, `MTWThF`)
- `Nth` — calendar_anchored (e.g., `1st`, `15th`, `3rd`)
- `last` — last day of month

### Verbose Creation with --attr

```bash
substrate entity create --type chore \
  --name "Clean cat litter" \
  --attr recurrence.schedule_type=interval \
  --attr recurrence.interval_days=2 \
  --attr recurrence.next_date_basis=completion
```

### One-Off Chores

Chores don't require recurrence. Create without `--every`:

```bash
substrate entity create --type chore --name "Fix the fence"
```

### Chore Lifecycle

1. Created with next_due calculated from schedule (or today for interval)
2. Heartbeat promotes to Ready when `next_due - lead_time_days <= today`
3. Complete: `update-entity.py UUID --resolution completed`
4. Trigger engine auto-resets: resolution to unresolved, focus to idle, life_stage to backlog, calculates new next_due, increments completion_count, updates streak
5. Back to step 2

### Completing a Recurring Entity

```bash
substrate entity update UUID --resolution completed
```

The `builtin:recurrence_reset` trigger fires automatically on completion for any entity with `schedule_type != none`. It resets the entity and calculates the next due date.

### Bringing Stale Dates Current

If an entity's next_due is far in the past (from neglect, long snooze, etc.):

```bash
substrate entity update UUID --bring-to-today
```

Resets next_due to the first valid scheduled date >= today. Also resets life_stage to backlog so the heartbeat can re-promote it.

## Updating an Entity

```bash
substrate entity update UUID [options]
```

Only specified fields change; everything else is preserved. The script updates both meta.yaml and SQLite, and handles bidirectional relationship writing.

**Engagement mode escalation:** If you update `engagement_mode` to `execute` (from `explore`, `wander`, or `none`), the engagement pack obligation activates immediately. Read the `engagement-pack` skill and produce doctrine and plan before proceeding with any implementation. Do not treat the update as a background metadata change.

**Common update patterns:**

```bash
# Mark a task as actively in progress
substrate entity update UUID --focus active --life-stage in_progress

# Complete a task
substrate entity update UUID --resolution completed --focus closed

# Add a relationship
substrate entity update UUID --belongs_to PARENT_UUID

# Rename
substrate entity update UUID --name "Better Name"

# Update description
substrate entity update UUID --description "Clearer description"

# Mark an entity as processed by an agent (idempotent, no duplicates)
substrate entity update UUID --mark-processed agent-name
```

Run with `--help` for all options.

## Querying Entities

Use `query.py` to find entities before updating them:

```bash
substrate query find "search term"     # Search by name
substrate query entity UUID             # Full details
substrate query pending                 # Unresolved work
substrate query active                  # Actively worked items
substrate query type task               # All of a type
substrate query children UUID           # Direct children
substrate query tree UUID               # Full hierarchy
substrate query dim focus active        # Query by dimension
substrate query by username             # Work by actor
substrate query unprocessed agent-name  # Unprocessed by agent
substrate query theme                   # All themes with entity counts
substrate query theme foundation        # All entities with a given theme
```

For complex queries, see the `entity-query` skill.

## Deleting an Entity

Default behavior is **soft delete (archive)**: sets `meta_status: archived` and records a timestamp. The entity drops out of all active queries immediately but remains on disk for 30 days.

```bash
# Soft delete (archive) — default
substrate entity delete UUID
substrate entity delete UUID1 UUID2 UUID3    # batch archive

# Hard delete — immediate permanent removal
substrate entity delete UUID --permanent --force

# Restore an archived entity
substrate entity delete --restore UUID

# Purge expired — hard delete all entities archived > N days ago (default: 30)
substrate entity delete --purge-expired
substrate entity delete --purge-expired --days 7

# Preview without changes
substrate entity delete UUID --dry-run
```

**Soft delete** sets `meta_status: archived` and `archived_at` in meta.yaml and SQLite. Relationships are preserved (entity can be restored cleanly). All queries filter `meta_status = 'live'`, so archived entities are invisible to normal operations.

**Hard delete** (`--permanent`) removes everything: files, SQLite records (entities + relationships), relationship references in neighbor meta.yaml files, vector embeddings, and empty shard directories.

**Restore** (`--restore UUID`) flips `meta_status` back to `live` and removes `archived_at`. Only works on archived entities.

All delete operations are logged to the CDC changelog.

## Batch Operations

For bulk creation or bulk status updates, use the batch scripts instead of calling create-entity.py or update-entity.py in a loop. One boot, one schema load, one DB connection, N operations.

### Batch Create (`batch-create.py`)

Takes a YAML manifest. All entities are validated before any writes — fails atomically if any entity is invalid.

**Manifest format:**
```yaml
# Optional defaults applied to every entity unless overridden
defaults:
  belongs_to: TICKET_UUID

entities:
  - type: task
    name: "Task 1"
    ref: t1                          # optional: local name for cross-manifest references
    description: "Do something"
    engagement_mode: execute
    importance_tactical: high
    attrs:
      recurrence.schedule_type: none

  - type: task
    name: "Task 2"
    depends_on: ref:t1               # ref: prefix resolves to the UUID of t1
    attrs:
      recurrence.schedule_type: none
```

**Key rules:**
- `ref:` prefix in a relationship value resolves to the UUID of the entry with that `ref` — must appear earlier in the manifest
- Relationships between entries in the same manifest get inverse relationships written automatically (no external meta.yaml update needed for within-batch targets)
- `attrs:` dict handles extra fields; `recurrence:` nested dict handles recurrence config
- Relationship keys (`belongs_to`, `depends_on`, etc.) are top-level keys, not under `attrs:`

```bash
python3 _system/scripts/batch-create.py --manifest entities.yaml
python3 _system/scripts/batch-create.py --manifest entities.yaml --dry-run
```

### Batch Update (`batch-update.py`)

Takes a YAML manifest of dimensional updates. Applies independently per entity; errors collected at end by default.

**Manifest format:**
```yaml
- id: UUID_OR_PREFIX
  life_stage: in_progress
  focus: active
- id: UUID_OR_PREFIX
  resolution: completed
  life_stage: done_working
  assessment: on_track
```

```bash
python3 _system/scripts/batch-update.py --manifest updates.yaml
python3 _system/scripts/batch-update.py --manifest updates.yaml --dry-run
python3 _system/scripts/batch-update.py --manifest updates.yaml --fail-fast  # stop on first error
```

**When to batch vs. single:**
- Single call: interactive work, one or two entities, agent claiming/unclaiming (requires concurrency flags)
- Batch: pipeline output, digest distillation, content ingestion, cascading status updates across many entities

## Change Data Capture (CDC)

Every create, update, and delete operation is automatically logged to an append-only changelog. Entries include old/new attribute diffs, relationship changes, agent identity, and cascade events. No action required from the caller; the scripts handle it.

Agent identity is captured from the `SUBSTRATE_AGENT` environment variable when set.

Query the changelog with `query.py changelog` (see entity-query skill).

## Example: Creating a Task

```bash
substrate entity create \
  --type task \
  --name "Review API Documentation" \
  --description "Ensure developer onboarding docs are accurate before Q2 launch." \
  --belongs_to 550e8400-e29b-41d4-a716-446655440020 \
  --importance-tactical high \
  --focus active \
  --life-stage in_progress
```

This produces a meta.yaml like:

```yaml
id: 550e8400-e29b-41d4-a716-446655440010
type: task
name: Review API Documentation
description: Ensure developer onboarding docs are accurate before Q2 launch.
meta_status: live
focus: active
life_stage: in_progress
assessment: not_assessed
importance_tactical: high
resolution: unresolved
created: 2026-02-23T10:30:00
modified: 2026-02-23T10:30:00
belongs_to:
  - 550e8400-e29b-41d4-a716-446655440020
```

The script also:
- Creates the sharded folder (`entities/task/55/0e/{uuid}/`)
- Inserts into SQLite
- Writes inverse relationship (`contains: {this-uuid}`) on the parent entity
