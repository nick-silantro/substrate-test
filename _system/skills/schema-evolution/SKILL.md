---
name: schema-evolution
description: Handle schema extension and customization requests. Use when user says "add a type", "add an attribute", "create a relationship", "rename this type", "alias X as Y", "hide this type", "I want to call goal objective", or similar schema modification requests. The managed schema is read-only — route additive changes to schema-user/, display overrides to overlay.yaml.
author: Nick Silhacek
version: 3.2.0
last_edited: 2026-05-03
---

# Schema Evolution

## Architecture (read before acting)

Two schema layers exist. Know which one applies before running anything.

**Managed schema** — `_system/schema/` inside the engine. Controlled by the operator; updated via `substrate update`. Never edit these files. If the user asks to modify a managed type, attribute, or relationship (rename, delete, restructure), surface to the operator.

**User schema** — workspace-local. Two locations:
- `_system/schema-user/` — additive extensions (new types, groupings, attributes, relationships)
- `_system/overlay.yaml` — display overrides (aliases, hidden elements)

All `substrate schema add` commands write to `_system/schema-user/` and check for conflicts automatically. `load_schema` merges both layers transparently — all downstream scripts see the combined result.

## Naming Conventions

- **Type names:** singular and kebab-case (`goal`, `client`, not `goals`, `clients`)
- **Type aliases:** same convention as the canonical name — singular (`objective`, not `objectives`)
- **Grouping names:** kebab-case, conventionally plural (`horizons`, `actions`) though less strictly enforced

## Adding Schema Elements

### New type

```bash
substrate schema add type NAME --grouping GROUPING --description "..."
```

The grouping must exist in the managed schema or in the user's extensions. If neither exists, create the grouping first. Type names are kebab-case singular.

### New grouping

```bash
substrate schema add grouping NAME --nature work|object --description "..."
```

Groupings are rare. Before creating one, check the managed schema and user extensions — if any existing grouping's nature fits, use it. Only create a new grouping when the category is genuinely distinct.

### New attribute

```bash
substrate schema add attribute NAME --data-type TYPE --description "..." [--on TYPE1,TYPE2] [--values A,B,C]
```

Data types: `string`, `url`, `text`, `boolean`, `integer`, `float`, `date`, `enum`, `list`, `uuid`. Use `--values` for enum data types.

**Scope (`--on`):** Omitting `--on` makes the attribute universal — any type can use it. `--on TYPE1,TYPE2` restricts it exclusively to those types. Respect the user's stated scope, but write descriptions that survive reuse: "City" is better than "City of the client" since the attribute may apply to other types later.

**Access default:** When scoping with `--on`, the attribute is `preferred` for those types unless the user explicitly says it's required. Don't default to required.

**Storage:** All attributes default to indexed storage — a SQLite column and index are created automatically when the command runs. No separate migration step is needed.

### New relationship

```bash
substrate schema add relationship NAME --inverse INVERSE --category CATEGORY --description "..."
```

Categories: `containment`, `origin`, `causal`, `associative`. Both the forward name and its inverse must be globally unique across the full schema.

## Aliasing Schema Elements

When the user wants to refer to a managed or user-defined element by a different name, create an alias. Aliases resolve transparently in CLI commands and agent conversations — the user can use either name.

```bash
substrate schema alias type CANONICAL ALIAS
substrate schema alias attribute CANONICAL ALIAS
substrate schema alias relationship CANONICAL ALIAS
```

Example: after `substrate schema alias type goal objective`, `substrate entity create objective` works identically to `substrate entity create goal`.

**Collision detection runs automatically at creation time.** The command will fail if:
- The alias target matches any canonical name in the same namespace
- The alias target is already used by a different alias

To remove an alias:
```bash
substrate schema unalias type CANONICAL
substrate schema unalias attribute CANONICAL
substrate schema unalias relationship CANONICAL
```

### Post-update collision check

After `substrate update`, a scan runs automatically and prints any new collisions to stderr. If the user reports unexpected alias behavior after an update, run:

```bash
substrate schema alias check
```

This reports any aliases now shadowed by canonical names the engine added. A canonical name always wins over a shadowed alias; the alias effectively stops working until renamed.

### Alias namespaces

Types, attributes, and relationships have separate alias namespaces. `objective` as a type alias does not conflict with `objective` as an attribute alias. Collision detection operates within each namespace independently.

## Hiding Schema Elements

When the user says they don't use certain types, attributes, or relationships and wants them out of the way:

```bash
substrate schema hide type NAME
substrate schema hide attribute NAME
substrate schema hide relationship NAME

substrate schema unhide type NAME
```

Hidden elements are suppressed from `substrate query types` output and Surface pickers. The underlying data and entities are unaffected.

## Viewing the Schema

When the user wants to see what's available:

```bash
substrate schema               # full schema reference
substrate query types          # all types (respects hidden)
substrate query schema TYPE    # attributes and relationships for a specific type
substrate query relationships  # all relationships
```

These show the fully merged schema — managed and user extensions combined.

## User schema files

After adding extensions, `_system/schema-user/` contains:

```
_system/schema-user/
  types.yaml          # custom types and groupings
  attributes.yaml     # custom attributes
  relationships.yaml  # custom relationships
```

These are plain YAML files. Use `substrate schema add` to modify them — direct edits bypass conflict checking and won't trigger SQLite migration for attributes that need columns.
