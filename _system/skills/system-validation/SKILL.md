---
name: system-validation
description: Verify system integrity and repair inconsistencies. Use when user says "check system health", "validate indexes", "something seems wrong", "repair system", or during scheduled maintenance. Detects orphaned entities, broken relationships, index drift, and schema violations.
author: Nick Silhacek
version: 0.4.0
last_edited: 2026-03-03
---

# System Validation

Ensure SQLite matches reality, relationships are bidirectional, and schema rules are followed. This skill is the immune system of the workspace.

## Quick Start

```bash
# Full validation — checks SQLite vs disk, bidirectional integrity, referential integrity, schema compliance
substrate validate

# Validate and auto-repair what's safe to fix
substrate validate --repair

# Quick health overview (counts only)
substrate query stats

# Full SQLite rebuild from meta.yaml files (fixes all index drift)
substrate index rebuild
```

If `validate.py` reports issues, `--repair` will fix SQLite drift and missing inverse relationships automatically. Dangling references, unknown types, and invalid dimensions require manual review.

## When to Validate

**User-triggered:** "Check system health", "validate my workspace", "something seems wrong"
**After operations:** Large batch operations, schema migrations, manual meta.yaml edits
**When suspicious:** Operation fails unexpectedly, entity lookup returns stale data

## Validation Checks

### 1. SQLite vs Disk Integrity

**What:** SQLite should contain exactly the entities that exist on disk.

**Check:**
1. Count entity folders on disk: `find entities -name "meta.yaml" | wc -l`
2. Count SQLite rows: `substrate query stats`
3. Compare — numbers should match

**Issues detected:**
- `missing_from_sqlite`: Entity folder exists but not in SQLite
- `ghost_in_sqlite`: SQLite row for non-existent entity folder
- `stale_data`: SQLite fields don't match current meta.yaml

**Repair:** Rebuild SQLite: `substrate index rebuild`

### 2. Bidirectional Relationship Integrity

**What:** Every relationship should have its inverse on the target entity.

**Check:**
1. Use `query.py relationships UUID` on entities to list their relationships
2. For each relationship, verify the target entity has the matching inverse
3. Check `_system/schema/relationships.yaml` for the correct inverse name

**Issues detected:**
- `missing_inverse`: Source has relationship, target missing inverse
- `orphan_inverse`: Target has inverse, source missing forward relationship

**Repair:**
- Add missing relationship to the appropriate entity's meta.yaml (update `modified` timestamp)
- Rebuild SQLite: `substrate index rebuild`

### 3. Schema Compliance

**What:** All entities should conform to their type's schema, and the schema files themselves should be internally consistent.

**Schema file consistency:**
```bash
substrate validate schema
```
Runs 18 cross-file checks: type references, grouping coverage, access declarations, inverse completeness, enum validity, connection rules, attribute collisions, etc. Run this after any schema change or when schema-related errors appear.

**Entity compliance check:**
1. For each entity (use `query.py type TYPE` to enumerate):
2. Verify type exists in `_system/schema/types.yaml`
3. Check dimensional status values are valid for the type's nature
4. Verify relationships use valid relationship names from the schema

**Issues detected:**
- `unknown_type`: Entity claims type not in schema
- `invalid_dimension`: Dimensional status value not in allowed set
- `disallowed_dimension`: Dimension present that the type shouldn't have (e.g., resolution on a pillar)
- `unknown_relationship`: Relationship name not in schema

**Repair:**
- `unknown_type`: Flag for user (create type or re-type entity)
- `invalid_dimension`: Flag for user correction, or reset to default
- `disallowed_dimension`: Remove the dimension from meta.yaml
- `unknown_relationship`: Flag for user (define relationship or remove)

### 4. Referential Integrity

**What:** All UUID references in relationships should resolve to existing entities.

**Check:**
1. Query all relationships from SQLite: `SELECT * FROM relationships`
2. For each target UUID, verify it exists: `query.py entity UUID`

**Issues detected:**
- `dangling_reference`: UUID in relationship doesn't resolve to any entity

**Repair:**
- Remove dangling relationship from meta.yaml
- Update `modified` timestamp
- Rebuild SQLite

## Validation Modes

### Report Only (default)

Generate report of all issues found without making changes.

```
System Validation Report
========================
Entities on disk: 87
Entities in SQLite: 87
Relationships in SQLite: 119

Issues Found: 3

Relationship Issues (2):
  - missing_inverse: task/abc → belongs_to → ticket/def (ticket missing contains)
  - dangling_reference: project/ghi → relates_to → 550e8400... (not found)

Schema Issues (1):
  - invalid_dimension: task/jkl focus="Working" (not a valid focus value)
```

### Interactive Repair

For each issue, prompt user:
```
Issue: missing_inverse — task/abc belongs_to ticket/def, but ticket missing contains
Action options:
  1. Add contains to ticket/def (recommended)
  2. Remove belongs_to from task/abc
  3. Skip (leave inconsistent)
```

### Auto Repair

Fix all issues that have safe automatic repairs:
- Rebuild SQLite (fixes all index drift)
- Add missing inverse relationships
- Remove dangling references

Flag for manual review:
- Unknown types (need user decision)
- Invalid dimension values (need user input)

## Running Validation

### Full System
```bash
substrate validate
```
Runs all 4 checks. Exit code 0 = clean, 1 = issues found, 2 = error.

### With Auto-Repair
```bash
substrate validate --repair
```
Fixes safe issues automatically:
- SQLite drift → rebuilds SQLite
- Missing inverses → writes inverse relationship to target entity's meta.yaml, rebuilds SQLite

Flags for manual review:
- Dangling references (which side to clean up?)
- Unknown types, invalid dimensions, unknown relationships (need user decision)

### Single Entity
Quick diagnostic: `substrate query entity UUID` — shows all fields, relationships, and path.

## Pre-Execution Validation (precheck.py)

Substrate has two layers of validation:

1. **System integrity** (`validate.py`) — checks the whole workspace after the fact. Is SQLite consistent with disk? Are relationships bidirectional? Are schema rules followed? This skill covers system integrity.

2. **Operation pre-checks** (`precheck.py`) — validates a single create/update operation against the schema before it runs. Invalid dimension values, forbidden fields, bad enum values, connection rule violations. Wired into `create-entity.py` and `update-entity.py` so operations are blocked before writing. Also available standalone for agents and Surface form validation.

```bash
# Standalone pre-check (same flags as entity scripts)
python3 _system/scripts/precheck.py create --type task --name "..." --focus Banana
python3 _system/scripts/precheck.py update UUID --focus active

# Module usage (for Surface, MCP, etc.)
from precheck import validate_create, validate_update
result = validate_create(schema, "task", dimensions={"focus": "Banana"}, db_path=DB_PATH)
```

See the `entity-management` skill for details on what pre-checks catch.

## Integration with Other Skills

**entity-management:** After bulk operations, suggest validation. Pre-checks are built into entity scripts.
**schema-evolution:** Run validation after schema changes
**staging-intake:** Validate newly created entities
**decision-traces:** Log all repair decisions
