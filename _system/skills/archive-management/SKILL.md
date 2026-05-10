---
name: archive-management
description: Archive entities and old decision traces. Use when user says "archive old traces", "what can be archived", "archive this", "delete entity", "restore entity", or "retrieve from archive". Entity archiving uses the CLI; trace archiving is manual file moves.
author: Nick Silhacek
version: 0.2.1
last_edited: 2026-05-10
---

# Archive Management

Archive content to reduce clutter while maintaining retrievability. Entity archiving is handled by the CLI (`substrate entity delete`); trace archiving is a manual file-move process.

## Entity Archiving

Entity archiving is built into `delete-entity.py`. The default behavior is **soft delete**: sets `meta_status: archived` in-place (no folder movement), drops the entity from all active queries, and logs the operation to the CDC changelog.

### Archive an Entity

```bash
# Soft delete (archive) — entity stays on disk, drops from queries
substrate entity delete UUID

# Batch archive
substrate entity delete UUID1 UUID2 UUID3

# Preview without changes
substrate entity delete UUID --dry-run
```

The script sets `meta_status: archived` and `archived_at` in both meta.yaml and SQLite. Relationships are preserved (no cleanup on soft delete), allowing clean restoration later.

### Restore an Archived Entity

```bash
substrate entity delete --restore UUID
```

Flips `meta_status` back to `live`, removes `archived_at`. The entity reappears in all queries. Only works on archived entities.

### Find Archived Entities

Archived entities are still in SQLite (with `meta_status = 'archived'`), just excluded from active queries. To find them:

```bash
# Direct SQLite query
sqlite3 _system/index/substrate.db "SELECT id, name, type, archived_at FROM entities WHERE meta_status = 'archived';"

# Check the CDC changelog for archive events
substrate query changelog --op delete
```

### Purge Expired Archives

Archived entities older than 30 days can be permanently removed:

```bash
# Hard delete all entities archived > 30 days ago
substrate entity delete --purge-expired

# Custom threshold
substrate entity delete --purge-expired --days 7
```

### Permanent Deletion

Skip archiving entirely and hard delete immediately:

```bash
substrate entity delete UUID --permanent --force
```

Hard delete removes: files, SQLite records (entities + relationships), relationship references in neighbor meta.yaml files, vector embeddings, and empty shard directories.

### Relationship Behavior

- **Soft delete (archive):** Relationships are preserved. Neighbor entities still list the archived UUID in their meta.yaml. Queries won't traverse to it (it's invisible), but the references remain for clean restoration.
- **Hard delete (permanent):** Relationships are cleaned up. The script finds all neighbors via SQLite, removes the deleted UUID from their meta.yaml relationship lists, and removes SQLite relationship records.

## Decision Trace Archiving

Old decision traces are the other thing this skill manages. This is a manual file-move process (no script).

**Source:** `/_system/logs/traces/`
**Destination:** `/_system/archive/traces/{YYYY-MM}/`

**Criteria:** Traces older than 90 days are candidates.

### "Archive old traces"

1. Identify traces older than threshold
2. Confirm count with user
3. Create archive folders as needed: `/_system/archive/traces/{YYYY-MM}/`
4. Move trace files
5. Report results

### "Find archived traces"

Search `/_system/archive/traces/` by filename or content.

```
> "Find archived traces about the API migration"

Found 3 traces in archive:
  - 2025-10-15: Decision to use REST over GraphQL
  - 2025-10-22: Exception granted for legacy endpoint
  - 2025-11-03: Migration timeline approved

Would you like to see any of these?
```

## User Commands Summary

| User Says | Action |
|-----------|--------|
| "Archive this entity" / "Delete this" | `substrate entity delete UUID` (soft delete) |
| "Permanently delete" | `substrate entity delete UUID --permanent --force` |
| "Restore [entity]" | `substrate entity delete --restore UUID` |
| "What's archived?" | SQLite query for `meta_status = 'archived'` |
| "Clean up old archives" | `substrate entity delete --purge-expired` |
| "Archive old traces" | Manual file move to `_system/archive/traces/` |
| "Find archived trace" | Search `_system/archive/traces/` |

## Best Practices

- **Soft delete is the default.** Entities remain recoverable for 30 days.
- **All operations are CDC-logged.** Archive, restore, and hard delete events appear in the changelog.
- **Traces are easy.** Archive them freely. Safe, reversible, reduces clutter.
- **User controls everything.** No automatic archiving. User explicitly requests each action.
- **Don't manually edit meta_status.** Always use `substrate entity delete` so the changelog, SQLite, and meta.yaml stay in sync.
