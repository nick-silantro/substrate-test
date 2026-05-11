# CLI Reference

The `substrate` command is the tool your agent uses to manage your workspace. In normal use, you ask the agent for things in plain language and it runs the right commands on your behalf. This page documents every command for users who want to understand what's happening under the hood, or who prefer to run commands directly in a terminal.

Every command accepts `--help` for full option details.

## Quick Reference

| What you want to do | Command |
|---------------------|---------|
| Find something by name | `substrate query find "name"` |
| Find something by topic (not sure of name) | `substrate query search "topic"` |
| See your open work | `substrate query pending` |
| Create a note, task, or project | `substrate entity create --type TYPE --name "Name"` |
| Mark something done | `substrate entity update UUID --resolution completed --focus closed` |
| Add a parent relationship | `substrate entity update UUID --belongs_to PARENT-UUID` |
| Check workspace health | `substrate validate` |
| Update Substrate | `substrate update` |

---

## Entity Commands

Entities are the core building blocks of your workspace — notes, tasks, projects, decisions, and more. See [Entity Types](entity-types.md) for the full list.

### Create an entity

```
substrate entity create --type TYPE --name "Name" --description "Description"
```

**Common options:**

| Flag | What it does |
|------|-------------|
| `--type TYPE` | Entity type (required) |
| `--name "Name"` | Entity name (required) |
| `--description "Desc"` | What this entity is for |
| `--belongs_to UUID` | Attach to a parent entity |
| `--relates_to UUID` | Link to a related entity |
| `--due DATE` | Set a due date (YYYY-MM-DD) |
| `--every SCHEDULE` | Set recurrence (e.g., `2d`, `MWF`, `1st`) |
| `--importance-tactical VALUE` | Priority: `critical`, `high`, `medium`, `low` |
| `--dry-run` | Preview without creating |

**Examples:**
```
substrate entity create --type note --name "Meeting notes from Friday"
substrate entity create --type task --name "Write proposal" --belongs_to abc123
substrate entity create --type project --name "Website redesign" --description "Full overhaul"
substrate entity create --type chore --name "Weekly review" --every MWF
```

### Update an entity

```
substrate entity update UUID [options]
```

Only the fields you specify change. Everything else stays the same.

**Common options:**

| Flag | What it does |
|------|-------------|
| `--name "New Name"` | Rename the entity |
| `--description "New desc"` | Update the description |
| `--life-stage VALUE` | Workflow stage: `backlog` `ready` `in_progress` `under_review` `done_working` |
| `--resolution VALUE` | Outcome: `completed` `cancelled` `deferred` `superseded` |
| `--focus VALUE` | Attention state: `idle` `active` `waiting` `paused` `closed` |
| `--importance-tactical VALUE` | Change priority |
| `--belongs_to UUID` | Add a parent relationship |
| `--relates_to UUID` | Add a general relationship |
| `--remove-rel "type:UUID"` | Remove a relationship |

**Examples:**
```
substrate entity update abc123 --resolution completed --focus closed
substrate entity update abc123 --life-stage in_progress
substrate entity update abc123 --name "Better name" --description "Clearer purpose"
```

### Delete an entity

By default, delete is a soft delete (archive). The entity disappears from queries but stays on disk for 30 days and can be restored.

```
substrate entity delete UUID              # Archive (recoverable)
substrate entity delete UUID1 UUID2       # Archive multiple
substrate entity delete UUID --dry-run    # Preview without changes
substrate entity delete UUID --permanent --force   # Permanent — cannot be undone
substrate entity delete --restore UUID    # Restore an archived entity
substrate entity delete --purge-expired   # Permanently remove archives older than 30 days
```

---

## Query Commands

Find and retrieve entities from your workspace. Use `find` when you know the name; use `search` when you know the topic but not the exact name — `search` matches by meaning, `find` matches by text.

### Find and browse

```
substrate query find "search term"          # Find by name
substrate query find "term" --type task     # Filter by type
substrate query entity UUID                 # Full details on one entity
substrate query type note                   # All entities of a type
substrate query stats                       # Workspace overview (counts by type)
substrate query relationships UUID          # All relationships for an entity
substrate query children UUID               # Direct children
substrate query tree UUID                   # Full hierarchy (recursive)
```

### Find by status

```
substrate query pending                     # All unresolved work
substrate query active                      # Work currently in progress
substrate query workable                    # Work ready to be picked up
substrate query dim focus active            # Filter by any dimension value
substrate query dim life_stage in_progress  # Another dimension example
substrate query by USERNAME                 # Work assigned to or performed by an actor
```

### Find by time

```
substrate query due                         # Overdue items
substrate query due 7                       # Due within 7 days
substrate query due --type chore            # Only chores
substrate query chores                      # All chores with streak and schedule info
substrate query chores --due                # Only due or overdue chores
```

### Find by meaning

```
substrate query search "agent coordination"  # Semantic search — finds by meaning, not name
```

### History

```
substrate query changelog              # Last 20 changes
substrate query changelog UUID         # History of a specific entity
substrate query changelog --last 50    # Last N changes
substrate query changelog --since 2026-03   # Changes since a date
```

---

## Schema Commands

Customize entity types, attributes, and relationships. See [Configuration](../learn/configuration.md) for more detail.

### Add new elements

```
substrate schema add type goal --grouping horizons --description "A specific outcome to reach"
substrate schema add attribute budget --on project --data-type integer --description "Budget in USD"
substrate schema add relationship funded_by --inverse funds --category associative --description "Funder"
substrate schema add grouping finances --nature object --description "Financial records"
```

### Create aliases

```
substrate schema alias type goal objective       # "objective" now works the same as "goal"
substrate schema unalias type goal               # Remove the alias
substrate schema alias check                     # Check for alias conflicts after an update
```

### Hide elements you don't use

```
substrate schema hide type script                # Remove from pickers and lists
substrate schema unhide type script              # Restore it
```

### Browse the schema

```
substrate schema                        # Full schema reference
substrate query types                   # All types (respects hidden)
substrate query schema task             # Attributes and relationships for a specific type
```

---

## Context Commands

Load named context documents into the agent's session. Your agent runs these at session start to orient itself — reading your workspace state, focus areas, and any agent-specific guidance before the conversation begins.

```
substrate context agent-orientation     # Load the agent's operational orientation
substrate context context-stack         # Load all active context documents for this workspace
substrate context NARRATIVE             # Load the workspace state context doc by name
substrate context USER                  # Load the user profile context doc by name
substrate context CONTEXT-DOC-NAME      # Load any named context document by name
```

Names correspond to context-doc entities in your workspace. Three are created during setup — `USER`, `NARRATIVE`, and `BULLETIN-BOARD` (which starts empty) — and are always available. Any additional context docs you create can be loaded the same way. Your agent handles these automatically at session start.

---

## Workspace Management

See what workspaces are registered on your machine and clean up ones you no longer use.

```
substrate workspaces                   # List all registered workspaces and their services
substrate remove PATH                  # Unregister background services for a workspace
substrate remove PATH --delete         # Unregister services and delete the workspace directory
```

`substrate remove` works even if the directory has already been deleted — useful for cleaning up orphaned background services left behind after a workspace folder was removed manually.

---

## Validation and Maintenance

Keep your workspace healthy.

```
substrate validate              # Full check: database, relationships, schema compliance
substrate validate --repair     # Same check, auto-fixes issues where safe to do so
substrate validate schema       # Schema files only
substrate index rebuild         # Rebuild the database from source files (fixes index issues)
```

Run `substrate validate` if something seems wrong — stale search results, an entity that should appear but doesn't, or unexpected behavior. Run `substrate validate --help` for the full option set, including confirmation of `--repair` behavior. Run `substrate index --help` for index rebuild options.

---

## Updates

```
substrate update                # Update the Substrate engine to the latest version
```

Your agent will mention available updates at the start of a session. You can run this command directly, or just tell the agent "update Substrate."

---

## What's next

- [How-To Guides](../learn/how-to-guides.md) — common tasks in recipe form
- [Entity Types](entity-types.md) — the full list of types and what each one is for
- [Configuration](../learn/configuration.md) — how to customize your workspace
- [Background Services](background-services.md) — what runs in the background and how to restart it
