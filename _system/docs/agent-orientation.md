# Agent Orientation

You are operating inside a Substrate instance. Substrate is a personal knowledge graph — everything is an **entity**: a typed object (note, task, project, decision, person) stored as a folder containing a `meta.yaml` file and optional content files. Entities connect through typed relationships. SQLite is a queryable index; the YAML files are always the source of truth.

Your job: create, update, link, and query entities using the `substrate` CLI. Read the relevant skill before doing anything non-trivial.

## How the workspace is organized

```
~/substrate/          ← your instance
  entities/           ← the knowledge graph (one folder per entity, sharded by type)
  assets/             ← large binary files (PDFs, images, videos)
  staging/            ← incoming files to process
  builds/             ← apps, tools, and other things you build with Substrate
  _system/            ← workspace data layer (SQLite index, logs, config)
  .claude/            ← Claude Code config, agent definitions, skills
  CLAUDE.md           ← session bootstrap
```

The engine (scripts, schema) lives at `~/.substrate/engine/` and is managed by the `substrate` CLI — you never need to touch it directly. Skills are available at `.claude/skills/` in your workspace — read the relevant skill before doing anything non-trivial.

## Working with entities

**Always use the `substrate` CLI. Never create folders, edit `meta.yaml`, or write raw SQL directly.**

### Create

```bash
substrate entity create --type note --name "My note"
substrate entity create --type task --name "Write brief" --belongs_to PROJECT_UUID
substrate entity create --type project --name "Website redesign" --description "Full site overhaul"
```

### Update

```bash
substrate entity update UUID --name "New name"
substrate entity update UUID --attr key=value
substrate entity update UUID --life-stage in_progress
substrate entity update UUID --resolution completed
substrate entity update UUID --relates_to OTHER_UUID
substrate entity update UUID --belongs_to PARENT_UUID
```

### Query

```bash
substrate query find "name fragment"       # search by name
substrate query find "term" --type task    # filter by type
substrate query type note                  # all entities of a type
substrate query entity UUID                # full detail on one entity
substrate query stats                      # workspace overview
substrate query relationships UUID         # all relationships for an entity
substrate query workable                   # work items available to pick up
```

### Delete

```bash
substrate entity delete UUID               # soft delete
substrate entity delete UUID --force       # permanent
```

All CLI commands accept `--help` for full flag documentation.

## Skills

Read the relevant skill before doing non-trivial operations:

```bash
substrate context entity-management        # creating and updating entities
substrate context relationship-management  # linking entities
substrate context schema-evolution         # adding custom types and attributes
substrate context system-validation        # validating workspace integrity
substrate context archive-management       # archiving completed work
```

## Entity types

Types are grouped by nature. **Work** types have a pipeline (life_stage, resolution); **object** types persist without one.

**Work — actions:** `task`, `ticket`, `chore`
**Work — efforts:** `project`, `workstream`, `incident`, `initiative`

**Knowledge:** `note`, `document`, `reference`, `decision`, `idea`, `inquiry`
**People:** `person`, `organization`
**Horizons:** `pillar`, `mission`, `goal`, `milestone`
**Events:** `meeting`
**Artifacts:** `article`, `script`, `product`
**Logs:** `friction`

Run `substrate query stats` for a count of what's in your workspace.

## Status dimensions for work entities

| Dimension | Values |
|-----------|--------|
| `life_stage` | `backlog` → `ready` → `in_progress` → `under_review` → `done_working` |
| `resolution` | `unresolved` → `completed` / `cancelled` / `deferred` / `superseded` |
| `focus` | `idle` / `active` / `waiting` / `paused` |
| `importance_tactical` | `critical` / `high` / `medium` / `low` |

Set via `--life-stage`, `--resolution`, `--focus`, `--importance-tactical` flags on `substrate entity update`.

## Content files

Every entity folder can hold Markdown files alongside its `meta.yaml`. To add written content to an entity, create a file inside its folder (e.g., `notes.md`, `summary.md`). There are no naming restrictions — use whatever name is meaningful. These files are yours to edit directly.

## Common relationships

| Relationship | Use for |
|---|---|
| `belongs_to` | Hierarchy: task → ticket → project |
| `relates_to` | General association between any two entities |
| `references` | One entity cites or points to another |
| `leads_to` | Causal: completing A enables B |
| `authored_by` | Who created or owns this entity |
| `produces` | Work that generates an artifact |

All relationship flags use snake_case: `--belongs_to`, `--relates_to`, `--authored_by`.

## Extending the schema

```bash
substrate schema add type recipe --grouping knowledge --description "A cooking recipe"
substrate schema add attribute budget --on project --data-type integer --description "Budget in USD"
substrate schema add attribute owner --on task --data-type string --required   # required attribute
substrate schema add relationship funded_by --inverse funds --category associative
substrate schema update type recipe --description "Updated description"
substrate schema delete type recipe
```

## Validation and indexing

```bash
substrate validate              # full workspace integrity check
substrate validate schema       # schema consistency only
substrate index rebuild         # rebuild SQLite from YAML (files are the source of truth)
```

## Hard rules

1. **Never edit `meta.yaml` directly** — use `substrate entity update`.
2. **Never edit schema YAML files directly** — use `substrate schema add/update/delete`.
3. **Never write raw SQL** — the SQLite index is rebuilt from YAML; manual SQL writes are overwritten on the next rebuild.
4. **Files are the source of truth.** If SQLite and YAML disagree, YAML wins. Rebuild with `substrate index rebuild`.
