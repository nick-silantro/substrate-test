---
name: relationship-management
description: Manage links between entities in a functional system. Use when user says "link", "connect", "associate", "add relationship", "remove link", "unlink", "show relationships", "what belongs to", "what depends on", or similar. Scripts handle bidirectional relationships and SQLite indexing.
author: Nick Silhacek
version: 0.5.0
last_edited: 2026-03-09
---

# Relationship Management

Manage links between entities. Scripts handle bidirectionality, meta.yaml updates, and SQLite indexing automatically.

## Core Rules

1. Every relationship is bidirectional — scripts handle both sides
2. Relationships store UUIDs only, never paths or names
3. Validate against `_system/schema/relationships.yaml` before creating
4. Always use scripts — create, remove, and change are all handled by `update-entity.py`

## Creating Relationships

### Via create-entity.py (at creation time)

```bash
substrate entity create \
  --type task \
  --name "Review API Docs" \
  --belongs_to TICKET_UUID
```

The script writes the relationship to the new entity's meta.yaml, writes the inverse (`contains`) to the target entity's meta.yaml, and inserts both directions into SQLite.

### Via update-entity.py (after creation)

```bash
substrate entity update ENTITY_UUID --belongs_to TARGET_UUID
```

Same bidirectional handling — updates both meta.yaml files and SQLite.

Multiple relationships can be added in one call:
```bash
substrate entity update UUID \
  --belongs_to PROJECT_UUID \
  --relates_to OTHER_UUID
```

## Finding Relationships

```bash
substrate query relationships UUID   # All relationships for entity
substrate query children UUID         # Direct children (contains)
substrate query tree UUID             # Full hierarchy
```

## Removing Relationships

```bash
substrate entity update ENTITY_UUID --remove-rel "belongs_to:TARGET_UUID"
```

Format: `rel_type:target_uuid`. The script removes the relationship from both meta.yaml files (source and target inverse), deletes both SQLite rows, and updates `modified` timestamps on both entities.

Multiple removals in one call:
```bash
substrate entity update UUID \
  --remove-rel "belongs_to:TARGET1" \
  --remove-rel "relates_to:TARGET2"
```

## Changing Relationship Types

```bash
substrate entity update ENTITY_UUID --change-rel "old_type:TARGET_UUID:new_type"
```

Format: `old_type:target_uuid:new_type`. Atomically removes the old relationship and adds the new one — both sides, both meta.yaml files, both SQLite directions.

## Relationship Categories

Four canonical categories. Fixed — no new categories may be added in deployed instances.

### Containment

Structural parent-child hierarchy. Universal — not restricted to any nature or grouping.

| Relationship | Inverse | Usage |
|--------------|---------|-------|
| belongs_to | contains | Universal structural hierarchy |
| child_of | parent_of | Biological or organizational lineage (person entities) |
| reports_to | manages | Organizational reporting structure (person entities) |
| member_of | has_member | Group membership |

### Origin

Where something came from. The emerged entity holds the forward relationship; the origin context is the target.

| Relationship | Inverse | Usage |
|--------------|---------|-------|
| comes_from | spawned | Emerged from a context (meeting, conversation, document) |
| derived_from | source_of | Built by transforming or extending the target |

### Causal

Dependency, enablement, purpose, and production.

| Relationship | Inverse | Usage |
|--------------|---------|-------|
| leads_to | follows_from | Forward outcome causality |
| serves | served_by | Purposeful contribution — work exists in service of an object |
| enables | depends_on | Hard prerequisite — must exist or complete first |
| produces | produced_by | Work generates an output (document, artifact, deliverable) |

### Associative

Role-based connections, participation, and general association.

| Relationship | Inverse | Usage |
|--------------|---------|-------|
| relates_to | relates_to | General association (symmetric) |
| references | referenced_by | Cites or points to |
| assigned_to | owns | Assignment of work or responsibility to an actor |
| attended | attendee | Actor participated in an event |
| stakeholder_in | has_stakeholder | Interest or involvement without ownership |
| authored_by | authored | Creator of a knowledge or artifact entity |
| replies_to | replied_by | Message reply chain — enables threading |
| watches | watched_by | Trigger monitors this entity for events |
| acts_on | acted_on_by | Trigger's action targets this entity |

## Choosing the Right Relationship

### Pick the category first

**Containment** — Is this a structural parent-child relationship? Does the source live within the scope of the target? Use `belongs_to` for almost all cases.

**Origin** — Does this describe where something came from? Note: the target is always an object-nature entity (meeting, conversation, document). Nothing `comes_from` a work entity — work produces things.

**Causal** — Does this describe what something enables, serves, leads to, or produces? The source is typically work-nature when the relationship describes output (`produces`). Use `serves` when work exists in service of an object (project serves a mission). Use `enables`/`depends_on` for hard sequencing. Use `leads_to`/`follows_from` for emergent outcomes.

**Associative** — Role, participation, general connection, or trigger wiring. If none of the above apply, it's Associative.

### belongs_to vs relates_to (the independence test)

If an entity would be meaningless or unlocatable without its parent, use `belongs_to`. If it stands alone and just happens to touch something, use `relates_to`.

`belongs_to` makes entities discoverable via `query.py tree` and `query.py children`. `relates_to` only appears in `query.py relationships`. When in doubt: "Would someone exploring the parent entity expect to find this?"

### Origin vs Causal

Both can feel like "this entity exists because of that one." The distinction is direction in time and intent.

**Origin is retrospective** — it traces history. Ask: "Where did this come from? What gave birth to it?" The emerged entity holds the forward relationship; the origin context is the target.

**Causal is prospective** — it expresses purpose, dependency, or output. Ask: "What is this for? What does it enable? What does it produce?"

The nature-based test: the target of `comes_from` and `derived_from` is always an object-nature entity (meeting, conversation, document). Nothing `comes_from` a work entity — work produces things, it doesn't birth them. If the target is a work entity, the relationship is Causal.

- Task `comes_from` meeting — emerged from a context → Origin
- Task `produces` document — intentional work output → Causal
- Project `serves` mission — exists in service of an object → Causal
- Task `leads_to` outcome — causes something to emerge → Causal (prospective, not origin)

### comes_from vs derived_from

- Task **comes_from** Meeting — born in a context
- Document **derived_from** Document — built by transforming the source

### leads_to vs enables vs serves vs produces

| Question | leads_to | enables | serves | produces |
|----------|----------|---------|--------|----------|
| Sequencing prerequisite? | No | Yes | No | No |
| Purposeful contribution to an object? | No | No | Yes | No |
| Generates an artifact? | No | No | No | Yes |
| General forward outcome? | Yes | No | No | No |

### authored_by vs assigned_to

- Document **authored_by** Person (creator)
- Task **assigned_to** Person (responsible party)

### references vs relates_to

- Document **references** Document (explicit citation)
- Task **relates_to** Task (general association)

## Connection Rules

Check `_system/schema/relationships.yaml` for:
- `required_relationships` — what relationships a type must have
- `no_same_type_nesting` — types that cannot contain themselves
- Relationship `notes` fields — guidance on expected entity types for specific relationships

## Bulk Operations

### Transfer Relationships

Move a task from Ticket A to Ticket B:

```bash
substrate entity update TASK_UUID \
  --remove-rel "belongs_to:TICKET_A_UUID" \
  --belongs_to TICKET_B_UUID
```

Both operations in one call — removes old, adds new, both sides handled automatically.
