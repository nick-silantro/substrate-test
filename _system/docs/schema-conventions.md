---
name: Schema Conventions
startup_for: [L0]
context_audience: [all]
---
# Schema Conventions

How the schema YAML files work, what the conventions mean, and how to extend them. This document is the centralized reference for meta-rules — the rules about how rules are written. Individual type and attribute definitions live in the schema files themselves; this doc explains the patterns they follow.

## Source Files

| File | What it defines |
|------|----------------|
| `_system/schema/types.yaml` | Entity types, groupings, grouping natures |
| `_system/schema/attributes.yaml` | Attributes per type, universal attributes, dimensional status model |
| `_system/schema/relationships.yaml` | Relationship categories, inverses, connection rules, query behavior |

All three files are YAML. They are the source of truth — not SQLite, not markdown, not the UI. Scripts read them via `schema.py`.

## Types and Groupings

Every entity type belongs to exactly one grouping. Groupings define the **nature** of their member types:

- **`["work"]`** — FLAIR dimensions available (focus, life_stage, assessment, importance_tactical, resolution)
- **`["object"]`** — HIP dimensions available (health, importance_strategic, phase)

Grouping names and type names should not overlap, though the `group:` prefix on grouping references (see below) makes disambiguation explicit regardless.

Current groupings: `actions` (work), `efforts` (work), `horizons` (object), `planning` (object), `utility` (object), `actors` (object), `knowledge` (object), `artifacts` (object), `events` (object), `logs` (object).

### One Nature Per Grouping

Every type in a grouping shares the same nature. This is a hard rule — nature is a characteristic of the grouping, not of individual types. If a type needs a different nature, it belongs in a different grouping.

### Nature Classification

Nature follows from function, not from abstract characteristics:

- **Work:** Has completion conditions. Something an agent claims, executes, and marks done. Tasks, tickets, projects — they resolve.
- **Object:** Persists with ongoing lifecycle independent of any agent. Documents, people, products, missions, decisions — they don't "complete" in the pipeline sense; they exist, evolve, and eventually retire.

The key test: if no agent is actively working on it, does it still exist and matter? Objects do. If the entity's reason for existing is tied entirely to being worked on, it's work-nature.

### Milestone Scope

Milestones fit time-bound, completable efforts: missions, goals, projects. They don't fit pillars — pillars are permanent and asymptotic; there are no checkpoints on an infinite horizon. Use a milestone when the checkpoint itself has strategic weight: it can be communicated, celebrated, referenced by future work, and tracked against a timeline. If the checkpoint is just a plan annotation, it doesn't need an entity.

A milestone is distinct from a theme. A milestone is a structured entity with its own lifecycle, relationships, and status dimensions. A theme is a lightweight string label on work items used to group tickets into planning batches. Milestones are navigational; themes are organizational.

## Design Principles

**Don't duplicate what dimensions provide.** Before adding an attribute, check whether an existing dimension already captures that concern. Severity, urgency, importance, and lifecycle stage are all handled by the eight dimensions. Work-nature entities have `importance_tactical` for urgency/severity. Object-nature entities have `importance_strategic` for priority/centrality. Adding an attribute that restates a dimension in different vocabulary creates drift and confusion. If the dimension's scale doesn't fit, that's a case for refining the dimension, not adding a parallel attribute.

## Attribute Definitions

Attributes use an attribute-centric access model. Each attribute declares which types can use it via access declarations (exclusive toggle + required/preferred gradations). Attributes live in three places in `attributes.yaml`: `universal` (every entity), `blocks` (named groups with shared access), and the `fields` section (all other attributes with individual access declarations). See the Attribute Access Doctrine context-doc for the full design.

### Data Types

- `string` — free text
- `uuid` — UUID v4
- `date` — ISO date (YYYY-MM-DD)
- `datetime` — ISO datetime (YYYY-MM-DDTHH:MM:SS)
- `url` — valid URL
- `email` — email address
- `enum` — must include a `values` list
- `reference` — pointer to another entity; must include `target_type`
- `list` — array; must include `item_type`

### Reference Attributes and target_type

Reference attributes point at other entities. The `target_type` attribute controls what types of entities the reference can point at. It accepts three forms:

**1. Type name** — a single specific type:
```yaml
target_type: "person"
```

**2. Array** — an explicit list of accepted types:
```yaml
target_type: ["user", "agent"]
```

**3. Grouping reference** — uses the `group:` prefix, expands to all types in that grouping:
```yaml
target_type: "group:actors"
# Resolves to: person, user, agent, organization
```

The `group:` prefix is mandatory — it makes the reference explicit and collision-proof. A bare grouping name (without the prefix) is treated as a type name and will fail validation if no such type exists.

Grouping references are the preferred approach when an attribute should accept any member of a functional category. They scale automatically — adding a new type to the grouping in `types.yaml` broadens every attribute that uses the grouping reference. No per-attribute edits needed.

**Resolution**: `schema.resolve_target_type()` handles all three forms. Strings starting with `group:` are resolved against the groupings in `types.yaml`. Plain strings are resolved as type names. Arrays resolve each element independently. Helper methods: `schema.is_grouping_ref(v)` and `schema.parse_grouping_ref(v)`.

**When to use which form:**
- **Grouping reference**: when the attribute conceptually accepts "any actor" or "any work item" — the semantic category matters more than the specific types. Preferred default.
- **Array**: when the attribute accepts a specific subset that doesn't correspond to any grouping (e.g., `["user", "agent"]` for work assignment — persons and organizations can't be assigned work).
- **Type name**: when the attribute must reference exactly one type.

**Design smell: mixing types and grouping refs in a single array.** While `["person", "group:actors"]` is technically valid (arrays resolve each element), it almost always indicates a problem. If you need the whole grouping, use `"group:actors"` alone. If you need specific types, list them explicitly. Mixing implies the grouping boundary doesn't quite fit — which usually means the grouping itself needs rethinking, not a hybrid workaround.

## Dimensional Status Model

Status is dimensional, not flat. Eight dimensions, each answering a different question. Types pick which dimensions they use based on their grouping's nature (the "buffet model").

### The Eight Dimensions

**HIP** (object-nature):
- `health` — How is this doing? (growing, stable, declining, problematic, undefined)
- `importance_strategic` — How central is this? (core, important, peripheral)
- `phase` — Where is this in its lifecycle? (forming, established, aging, retired)

**FLAIR** (work-nature):
- `focus` — Am I working on this right now? (idle, active, waiting, paused, closed)
- `life_stage` — Where is this in its workflow? (backlog, ready, in_progress, under_review, done_working)
- `assessment` — How is it going / how did it go? (delivery: on_track/at_risk/off_track; outcome: exceeded/succeeded/mixed/failed)
- `importance_tactical` — How urgent is this? (critical, high, medium, low)
- `resolution` — Is this done? (unresolved, completed, cancelled, deferred, superseded)

### Dimension Categories

Each dimension has a category per type:

- **required** — must always be filled (validation enforced)
- **preferred** — should be shown and prominent (presentation guidance)
- **allowed** — available but not prominent
- **disallowed** — cannot be used with this type (validation enforced)

Defaults come from grouping nature. Each dimension declares which types/natures get required, preferred, or forbidden access via the same attribute-centric access model used by regular attributes. Per-type exclusions within a nature use `forbidden.types`.

### The Nature Boundary

The nature boundary is absolute. An object-nature type cannot gain FLAIR dimensions. A work-nature type cannot gain HIP dimensions. Dimension access declarations can restrict within a nature but cannot cross it. If a type genuinely needs dimensions from the other side, that's a signal it belongs in a different grouping — that is a schema evolution decision, not an access-level tweak.

### Type-Specific Defaults (type_defaults)

Dimensions can have per-type default overrides via an optional `type_defaults` key. When an entity of a matching type is created, that value is used instead of the global `default`.

```yaml
phase:
  values: [forming, established, aging, retired]
  type_defaults:
    agent: "established"  # runtime agents are operational when created
```

The global `default` still applies to all other types. Both are validated by `validate_schema.py` — values must be in the dimension's values list, and type names must exist in types.yaml.

Use `type_defaults` when a semantically correct default varies by type and null would be misleading. Leave a dimension null (no default at all) when null genuinely means "not yet assessed" — defaulting would make an unassessed entity look like it has been considered.

### Grouping-Level vs. Type-Level Lifecycle Dimensions

The default for custom lifecycle dimensions is grouping-level: one dimension covering all types in the grouping, with shared vocabulary. This prevents proliferation.

A type may define its own type-level lifecycle dimension when its pipeline is genuinely distinct, externally recognizable, and stable — and forcing it into shared grouping vocabulary would obscure rather than organize. The bar is high. Use `exclusive: true` with `preferred.types: ["X"]`. The vocabulary must be earned, not claimed speculatively.

Example: `application_status` on `job-opportunity` uses job-search industry vocabulary (saved/applied/screening/interviewing/offer/closed) that doesn't map cleanly to generic opportunity funnel stages. The type earns its own dimension.

Do not solve this problem by creating a new grouping for each type — that trades dimension proliferation for grouping proliferation, same mess different layer.

### Dimension Permanence

Dimensions are permanent — they cannot be added or removed. Their bundled values are non-removable but globally renamable. Users can add custom values to any dimension.

## Relationship Conventions

### Bidirectionality

Every relationship has an inverse. When entity A `relates_to` entity B, entity B `relates_to` entity A. Scripts manage both sides automatically — both the SQLite rows and the meta.yaml entries.

### Categories

Relationships are grouped into four canonical categories: containment, origin, causal, associative. These are fixed — no new categories may be added in deployed instances. Recursion is never implied by category membership; all traversal depth is specified explicitly at query time.

See `_system/schema/relationships.yaml` for the full category definitions and relationship inventory. See the relationship-management skill for the agent decision guide.

### Connection Rules

By default, any entity can connect to any other using any relationship. Guidance on expected types lives in the `notes` attribute on each relationship definition in `relationships.yaml` — informational, not enforced.

`no_same_type_nesting` prevents work types from structurally nesting entities of the same type (no task-inside-task, no project-inside-project). Scoped to containment relationships via `scope_category` — sequencing relationships like `depends_on`/`enables` between same-type entities are allowed.

### Relationship Naming

Relationship names should be present-tense or atemporal — usable at planning time, mid-execution, and after completion without implying that any specific event has occurred.

**Preferred:** `produces`, `contains`, `depends_on`, `enables`, `relates_to`
**Avoid:** `delivered`, `reported`, `completed`, `caused` — past-tense names imply a moment in time the relationship cannot represent

The reason: a relationship is a structural connection, not an event record. If A `produces` B, that relationship can be declared when the work is planned, remains true during execution, and describes what happened after completion. A relationship named `delivered` can only honestly be applied after delivery — which means agents working at planning time can't use it without lying about the state of work. Atemporality is what keeps the graph readable across the full lifecycle.

When temporal information matters, put it on entity fields (due dates, created timestamps, completion dates), not in the relationship name.

### Required Relationships

Some types require a relationship to exist (e.g., tasks must belong to a ticket). These are enforced at creation time with a 24-hour grace period.

## Type Governance

### Naming Principle: Avoid Colloquial Collision

Type names should not collide with common conversational words in contexts where the collision causes confusion. The test: could a non-technical user reasonably interpret the type name as something other than an entity type?

Example: `question` collides with "I have a question" — a casual ask, not a tracked open inquiry. A user told to "create a question" might not understand they're creating a persistent entity with resolution criteria. Prefer a more specific word that carries the entity's actual meaning (`inquiry` instead of `question`).

This matters most for types that non-technical users interact with directly. CLI-only types where the schema is developer-facing have more latitude.

### Adding a New Type

1. Does an existing type fit? Types can have flexible attributes.
2. Is this truly a different kind of thing, or just a variant?
3. What grouping does it belong to?
4. Does the name pass the colloquial collision test? (See naming principle above.)
5. Define in `types.yaml`, add attributes in `attributes.yaml`, add the type to relevant attribute/dimension access declarations if needed.
6. Run `validate_schema.py` to confirm consistency.

### Adding a New Grouping

Rare. Groupings represent fundamentally different categories of being. Requires a schema evolution decision.

### The No-Retyping Rule

Entities don't change type through lifecycle. If A produces B, they're separate entities linked by a relationship (e.g., `produces`).

## Theme Attribute

`theme` is an optional string attribute on work-nature entities. It provides a lightweight label for grouping tickets into planning batches without requiring a new entity type.

- **Type:** free-form string. Not an enum — naming conventions vary by project (e.g., `foundation`, `delivery`, `1`, `2`, `polish`)
- **Access:** exclusive to work-nature types; optional, never required
- **Usage:** set via `--attr theme=foundation` at creation or update
- **Query:** `query.py theme` lists all themes with counts; `query.py theme foundation` lists all entities with that theme

Distinct from milestones: a milestone is a structured entity with its own lifecycle, relationships, and status dimensions. A theme is a label on existing work items — no entity is created, no lifecycle is tracked. Use theme to express planning organization; use milestones for navigational checkpoints with strategic weight.

## Work-Level Assignment

Assignment is expressed through relationships, not attributes:

- **Tasks**: `performed_by` relationship (task → actor, cardinality: many) — who does the work. Inverse: `performed`.
- **Tickets/Projects/etc.**: `assigned_to` relationship (entity → actor) — who is responsible. Inverse: `owns`.
- **All work levels**: `reviewed_by` relationship (entity → actor) — outside perspective who verifies completion. Inverse: `reviews`.

Management is inferred from containment — the entity responsible via `assigned_to` implicitly manages everything beneath it in the hierarchy.

`processed_by` (universal attribute) is orthogonal — it tracks which agents have read/processed an entity, not who is responsible for it. It remains a flat attribute, not a relationship.

### Storage

Assignment relationships live in the `relationships` table (source_id, relationship, target_id). They are not columns on the entities table.

`processed_by` remains a comma-separated string on the entities table, queryable via `LIKE`.

Use `query.py actor ACTOR_REF` to find work performed by or assigned to an actor. Use `query.py unprocessed AGENT` to find entities an agent hasn't processed, and `update-entity.py UUID --mark-processed AGENT` to mark an entity as processed (idempotent).

## Attribute Exclusion

Attribute access is controlled by the attribute, not the type. Attributes with `exclusive: true` are forbidden for any type not listed in their required/preferred access declarations — no per-type configuration needed. For dimensions specifically, `forbidden.types` provides per-type exclusions within an otherwise-allowed nature. The result is the same — certain types cannot use certain attributes — but the declaration lives on the attribute/dimension, not on the type.

## Validation

Substrate validates at three levels:

1. **Schema consistency** (`validate_schema.py`) — cross-file checks ensuring types.yaml, attributes.yaml, and relationships.yaml are internally consistent. Run after any schema change.
2. **Operation pre-checks** (`precheck.py`) — validates individual create/update operations before execution. Catches invalid dimension values, forbidden fields, bad enum values, connection rule violations, and missing required relationships. Wired into `create-entity.py` and `update-entity.py`; also available standalone and as a module for Surface/MCP.
3. **System integrity** (`validate.py`) — post-hoc workspace health checks (SQLite vs. disk, bidirectional relationships, referential integrity, schema compliance).

### Schema Consistency (validate_schema.py)

`_system/scripts/validate_schema.py` runs semantic checks across the three schema files. Current checks (19):

1. Access declarations reference valid types and natures
2. Grouping membership: every type listed in a grouping exists in types.yaml
3. Grouping coverage: every type appears in at least one grouping
4. Reference target validity: target_type elements are valid type names or `group:` references
5. Required relationship target types are valid
6. Inverse completeness: every inverse exists as a relationship name
7. Enum fields have values (universal + attributes/blocks)
8. Enum defaults are in their values list (universal + attributes/blocks)
9. Required relationship types exist in types.yaml
10. Connection rule source/target types exist
11. no_same_type_nesting applies_to types exist
12. Grouping natures are valid arrays of "work" and/or "object"
13. No attribute/block attribute name collides with universal attributes
14. No attribute in the attributes section collides with a block attribute name
15. Every attribute in the attributes section has an access declaration
16. Every dimension has an access declaration
17. Exclusive attributes/dimensions have at least one type or nature in required/preferred
18. forbidden key only appears on dimensions, not on attributes or blocks
19. type_defaults validity: values are valid enum members; types exist in types.yaml; dimension is not disallowed for the referenced type

Run it after any schema change: `python3 _system/scripts/validate_schema.py`

## Schema Loader

`_system/scripts/schema.py` provides programmatic access to all schema data. Key methods:

- `schema.known_types` — set of all type names
- `schema.known_groupings()` — set of all grouping names
- `schema.resolve_target_type(t)` — resolve type/grouping ref/array to set of concrete types
- `schema.is_grouping_ref(v)` — check if a value has the `group:` prefix
- `schema.parse_grouping_ref(v)` — extract grouping name from `group:name`, or None
- `schema.type_attrs(t)` — attribute definitions for type t
- `schema.nature(t)` — grouping nature as list (["work"] or ["object"]) for type t
- `schema.dimension_config(t)` — dimension categories for type t
- `schema.inverses` — relationship to inverse mapping
- `schema.grouping_types(g)` — types in grouping g

All scripts that need schema data should use `from schema import load_schema` rather than parsing YAML directly.
