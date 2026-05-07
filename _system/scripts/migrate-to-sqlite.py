#!/usr/bin/env python3
"""
Migrate Substrate markdown indexes to SQLite.
Reads all entity meta.yaml files as source of truth, builds substrate.db.

Usage: python3 migrate-to-sqlite.py [SUBSTRATE_PATH]
Default SUBSTRATE_PATH is current directory.
"""

import os
import sys
import sqlite3
import glob
import yaml
from datetime import datetime
from schema import load_schema

SUBSTRATE_PATH = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SUBSTRATE_PATH", os.getcwd())
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")
schema = load_schema(SUBSTRATE_PATH)

def parse_meta(filepath):
    """Parse a meta.yaml file into a dict. Returns None on parse error."""
    try:
        with open(filepath, 'r') as f:
            meta = yaml.safe_load(f)
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict):
        return None
    # Ensure all values are strings or lists of strings for consistency
    for key, value in meta.items():
        if isinstance(value, (int, float)):
            meta[key] = str(value)
        elif isinstance(value, list):
            meta[key] = [str(v) for v in value]
    return meta


def get_entity_path(filepath):
    """Get the relative entity path from the full filepath."""
    # e.g., /path/to/substrate/entities/task/ab/cd/uuid/meta.yaml -> entities/task/ab/cd/uuid
    rel = os.path.relpath(os.path.dirname(filepath), SUBSTRATE_PATH)
    return rel


def pre_flight_check(meta_files):
    """Parse all entity YAML files and return a list of (filepath, error_message) tuples.

    Called before any destructive operations. If this returns any errors,
    abort without touching the database — the existing index stays intact.
    """
    errors = []
    for filepath in meta_files:
        try:
            with open(filepath, 'r') as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict):
                errors.append((filepath, "parsed as non-dict (empty or malformed)"))
        except (yaml.YAMLError, OSError) as e:
            errors.append((filepath, str(e)))
    return errors


def main():
    # Collect entity files first — pre-flight runs before any destructive steps
    pattern = os.path.join(SUBSTRATE_PATH, "entities", "**", "meta.yaml")
    meta_files = glob.glob(pattern, recursive=True)

    # Pre-flight: parse all YAML files before touching the DB
    preflight_errors = pre_flight_check(meta_files)
    if preflight_errors:
        print(f"Pre-flight check failed — {len(preflight_errors)} entity file(s) have YAML parse errors.")
        print("Fix these files before re-running migration (existing database preserved):\n")
        for filepath, error in preflight_errors:
            rel = os.path.relpath(filepath, SUBSTRATE_PATH)
            print(f"  {rel}")
            for line in error.splitlines():
                print(f"    {line}")
            print()
        print("Edit the files manually to correct the YAML, then re-run migration.")
        sys.exit(1)

    # Build into a temp file so the live DB is never touched until the rebuild
    # is fully complete. If interrupted mid-run, the live DB is untouched.
    tmp_path = DB_PATH + ".tmp"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)

    conn = sqlite3.connect(tmp_path)
    c = conn.cursor()

    # Create tables.
    #
    # The `entities` table is built in two passes:
    #   1. This hardcoded CREATE TABLE (below) — core columns that have special
    #      value derivation, non-TEXT types, or are SQLite-only (no meta.yaml
    #      counterpart). Keep hardcoded because code elsewhere depends on them
    #      by name + type (INTEGER vs TEXT, NULL handling, etc.).
    #   2. Schema-driven ALTER TABLE loop (further down) — reads attributes.yaml
    #      and adds a TEXT column for every attribute with storage: indexed or
    #      storage: column. This is where most of the table shape comes from.
    #
    # So if you're looking for where a specific column comes from: core columns
    # are hardcoded here; anything else was declared in _system/schema/attributes.yaml.
    c.executescript("""
        CREATE TABLE entities (
            id TEXT PRIMARY KEY,
            name TEXT,
            type TEXT NOT NULL,
            description TEXT,
            path TEXT NOT NULL,
            meta_status TEXT DEFAULT 'live',
            -- Dimensional status columns
            health TEXT,
            importance_strategic TEXT,
            phase TEXT,
            focus TEXT,
            life_stage TEXT,
            assessment TEXT,
            importance_tactical TEXT,
            resolution TEXT,
            --
            due TEXT,
            created TEXT,
            last_edited TEXT,
            -- Recurrence columns (universal, read from meta.yaml)
            next_due TEXT,
            last_completed TEXT,
            completion_count INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            snoozed_from TEXT,
            snoozed_until TEXT,
            recurrence_schedule TEXT,  -- JSON blob of recurrence config
            -- Engagement mode (type-specific, immutable after creation)
            engagement_mode TEXT,
            -- Theme (optional string label for grouping work items into planning batches)
            theme TEXT,
            -- Agent processing tracking (universal, read from meta.yaml)
            -- Note: performed_by and owner were removed — assignment is now via relationships table.
            processed_by TEXT,   -- comma-separated agent names
            -- Claim columns (SQLite-only, not in meta.yaml — transient coordination state)
            claimed_by TEXT,
            claimed_at TEXT,
            -- Actor identifier (user/agent short name for CLI resolution)
            handle TEXT,
            -- Agent attributes
            config_path TEXT,
            -- Asset attributes (asset grouping)
            asset_path TEXT,
            file_format TEXT,
            -- Dependency blocking (system-managed boolean)
            is_blocked TEXT,
            -- Trigger runtime state (not declared in attributes.yaml — INTEGER typing)
            last_fired TEXT,
            fire_count INTEGER
        );

    """)

    # --- Schema-driven column generation ---
    #
    # The CREATE TABLE above holds the core columns: identity, timestamps,
    # coordination state, and recurrence/trigger runtime. Those are tightly
    # coupled to code paths throughout the scripts and stay hardcoded.
    #
    # Below, we add columns driven by attributes.yaml:
    #   - Grouping-level dimension columns (dimensions section)
    #   - Scalar attribute columns (attributes section, storage: indexed — default)
    #   - List attribute columns (attributes section, list types with storage: indexed)
    #
    # Adding a new indexed attribute to attributes.yaml produces a column on the
    # next migration — no hardcoded list to update. Replaces the prior migration-
    # guard list for handle, config_path, asset_path, event_type, etc.

    # Columns already present from CORE_CREATE_TABLE — used to avoid duplicates.
    _core_cols = {
        "id", "name", "type", "description", "path", "meta_status",
        "health", "importance_strategic", "phase", "focus", "life_stage",
        "assessment", "importance_tactical", "resolution",
        "due", "created", "last_edited",
        "next_due", "last_completed", "completion_count", "streak",
        "snoozed_from", "snoozed_until", "recurrence_schedule",
        "processed_by", "claimed_by", "claimed_at",
        "engagement_mode", "theme", "handle", "is_blocked",
        "last_fired", "fire_count",
    }

    # Grouping-level dimension columns (schema-driven).
    _standard_dim_cols = {
        "health", "importance_strategic", "phase", "focus",
        "life_stage", "assessment", "importance_tactical",
        "resolution", "meta_status"
    }
    for _dim in schema.dimension_names:
        if _dim not in _standard_dim_cols and _dim not in _core_cols:
            try:
                c.execute(f"ALTER TABLE entities ADD COLUMN {_dim} TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

    # Scalar + list attribute columns (schema-driven).
    # Every attribute in attributes.yaml with storage tier "indexed" (default)
    # or "column" (column without an index) gets a SQLite column here.
    # "file_only" attrs are meta.yaml-only and skipped.
    # Whether each column gets an index is resolved separately below.
    for _attr in schema.columned_scalar_attrs() + schema.columned_list_attrs():
        if _attr in _core_cols:
            continue  # already handled above
        try:
            c.execute(f"ALTER TABLE entities ADD COLUMN {_attr} TEXT")
        except sqlite3.OperationalError:
            pass  # Column already exists

    c.executescript("""
        CREATE TABLE relationships (
            source_id TEXT NOT NULL,
            relationship TEXT NOT NULL,
            target_id TEXT NOT NULL,
            PRIMARY KEY (source_id, relationship, target_id),
            FOREIGN KEY (source_id) REFERENCES entities(id),
            FOREIGN KEY (target_id) REFERENCES entities(id)
        );

        -- Indexes for common queries (hardcoded foundational set).
        -- Additional indexes for every schema-declared indexed attribute are
        -- created below via the schema-driven loop.
        CREATE INDEX idx_entities_type ON entities(type);
        CREATE INDEX idx_entities_meta_status ON entities(meta_status);
        CREATE INDEX idx_entities_focus ON entities(focus);
        CREATE INDEX idx_entities_resolution ON entities(resolution);
        CREATE INDEX idx_entities_life_stage ON entities(life_stage);
        CREATE INDEX idx_entities_phase ON entities(phase);
        CREATE INDEX idx_relationships_source ON relationships(source_id);
        CREATE INDEX idx_relationships_target ON relationships(target_id);
        CREATE INDEX idx_relationships_type ON relationships(relationship);
        CREATE INDEX idx_entities_claimed_by ON entities(claimed_by);
        CREATE INDEX idx_entities_next_due ON entities(next_due);
        CREATE INDEX idx_entities_snoozed_until ON entities(snoozed_until);
        CREATE INDEX idx_entities_snoozed_from ON entities(snoozed_from);

        -- Change Data Capture (CDC) log.
        -- Written directly by changelog.py on every entity mutation.
        -- History resets on full database rebuild — that is acceptable.
        CREATE TABLE changelog (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            operation TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_name TEXT,
            agent TEXT,
            triggered_by TEXT,
            raw_json TEXT NOT NULL
        );

        CREATE INDEX idx_changelog_entity ON changelog(entity_id);
        CREATE INDEX idx_changelog_agent ON changelog(agent);
        CREATE INDEX idx_changelog_timestamp ON changelog(timestamp);
        CREATE INDEX idx_changelog_operation ON changelog(operation);

        -- File-level claims (Layer 3 concurrency control).
        -- Transient coordination state — cleared on rebuild, not in meta.yaml.
        CREATE TABLE file_claims (
            file_path TEXT PRIMARY KEY,
            agent_name TEXT NOT NULL,
            task_id TEXT,
            claimed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );

        -- Full-text search index for hybrid keyword+semantic search.
        -- Rebuilt from scratch on every migrate run. FTS5 with porter stemmer
        -- so "watch" matches "watcher", "watching", "watches".
        CREATE VIRTUAL TABLE IF NOT EXISTS fts_entities USING fts5(
            entity_id UNINDEXED,
            name,
            description,
            tokenize='porter unicode61'
        );
    """)

    # Schema-driven indexes — one per "indexed"-tier attribute and one per
    # grouping-level dimension. Attributes with `storage: "column"` get a
    # column via the ALTER loop above but NO index here (that's the point of
    # the column tier). file_only attrs are absent from both passes.
    #
    # The hardcoded executescript above covers the hot query paths (status
    # dims, timestamps, coordination). This loop adds an index for every
    # additional indexed attribute so filter/sort stays fast at scale.
    # IF NOT EXISTS guards against collisions with the hardcoded set.
    _already_indexed = {
        "type", "meta_status", "focus", "resolution", "life_stage", "phase",
        "claimed_by", "next_due", "snoozed_until", "snoozed_from",
    }
    _all_indexed_cols = (
        [d for d in schema.dimension_names]
        + schema.indexed_scalar_attrs()
        + schema.indexed_list_attrs()
    )
    for _col in _all_indexed_cols:
        if _col in _already_indexed:
            continue
        try:
            c.execute(f"CREATE INDEX IF NOT EXISTS idx_entities_{_col} ON entities({_col})")
        except sqlite3.OperationalError:
            pass  # Column may not exist (e.g., core-only column)

    # Create vec_entities virtual table if sqlite-vec is available
    try:
        from embeddings import is_search_available, load_vec_extension, init_vec_table
        if is_search_available() and load_vec_extension(conn):
            init_vec_table(conn)
            print("  vec_entities table created (run rebuild-embeddings.py to populate)")
    except ImportError:
        pass  # Search not set up yet — that's fine

    entity_count = 0
    rel_count = 0
    errors = []

    for filepath in meta_files:
        meta = parse_meta(filepath)
        if not meta or 'id' not in meta:
            errors.append(f"Skipped (no id): {filepath}")
            continue

        entity_path = get_entity_path(filepath)

        # Insert entity
        try:
            # Normalize list values to comma-separated strings
            # Note: performed_by and owner were removed — assignment is now via relationships table.
            processed_by_raw = meta.get('processed_by')
            if isinstance(processed_by_raw, list):
                processed_by_val = ','.join(str(v).strip() for v in processed_by_raw)
            elif processed_by_raw:
                processed_by_val = str(processed_by_raw).strip()
            else:
                processed_by_val = None

            # Serialize recurrence config to JSON for SQLite.
            # Strip runtime tracking attributes — these have their own flat columns
            # and are not part of the schedule config blob.
            _RECURRENCE_RUNTIME = {"next_due", "last_completed", "completion_count", "streak", "last_fired", "fire_count"}
            recurrence_raw = meta.get('recurrence')
            recurrence_json = None
            if isinstance(recurrence_raw, dict):
                import json
                recurrence_config_only = {k: v for k, v in recurrence_raw.items()
                                          if k not in _RECURRENCE_RUNTIME}
                recurrence_json = json.dumps(recurrence_config_only) if recurrence_config_only else None

            # Build INSERT dynamically.
            #
            # Three column groups:
            #   1. Core columns — identity, timestamps, recurrence runtime, coordination.
            #      These have special value derivation (e.g., recurrence_schedule is JSON,
            #      completion_count is INTEGER, is_blocked needs normalization).
            #   2. Grouping-level dimension columns — read straight from meta[dim_name].
            #   3. Schema-derived attribute columns — every indexed scalar/list attribute.
            #      Scalars are read as-is (with per-attr special cases); lists are CSV-joined.

            # Grouping-level dimensions (e.g., delivery_status, payment_status, pipeline_status).
            _standard_dim_cols = {
                "health", "importance_strategic", "phase", "focus",
                "life_stage", "assessment", "importance_tactical",
                "resolution", "meta_status"
            }
            grouping_dims = [d for d in schema.dimension_names if d not in _standard_dim_cols]

            # Serialize action_parameters dict to JSON for SQLite storage.
            _action_params_raw = meta.get('action_parameters')
            _action_params_json = (
                json.dumps(_action_params_raw)
                if isinstance(_action_params_raw, dict)
                else _action_params_raw  # already a string or None
            )

            # Normalize is_blocked to lowercase string 'true'/'false'.
            # YAML parses true/false as Python bool, but parse_meta() converts
            # bools to str via isinstance(value, int) — so we may get 'True'/'False'
            # (capitalized) or True/False (bool). Normalize all variants.
            _is_blocked_raw = str(meta.get('is_blocked', '')).lower()
            _is_blocked_val = (
                'true' if _is_blocked_raw == 'true'
                else 'false' if _is_blocked_raw == 'false'
                else None
            )

            # Core columns — hardcoded because of special value derivation.
            # These are the columns in CORE_CREATE_TABLE plus standard dims.
            core_cols = [
                "id", "name", "type", "description", "path", "meta_status",
                "health", "importance_strategic", "phase", "focus", "life_stage",
                "assessment", "importance_tactical", "resolution",
                "next_due", "last_completed", "completion_count", "streak",
                "snoozed_from", "snoozed_until", "recurrence_schedule",
                "processed_by",
                "due", "created", "last_edited",
                "is_blocked", "last_fired", "fire_count",
            ]
            core_vals = [
                meta.get('id'),
                meta.get('name'),
                meta.get('type', 'unknown'),
                meta.get('description'),
                entity_path,
                meta.get('meta_status', 'live'),
                meta.get('health'),
                meta.get('importance_strategic'),
                meta.get('phase'),
                meta.get('focus'),
                meta.get('life_stage'),
                meta.get('assessment'),
                meta.get('importance_tactical'),
                meta.get('resolution'),
                (meta.get('recurrence') or {}).get('next_due'),
                (meta.get('recurrence') or {}).get('last_completed'),
                int((meta.get('recurrence') or {}).get('completion_count', 0) or 0),
                int((meta.get('recurrence') or {}).get('streak', 0) or 0),
                meta.get('snoozed_from'),
                meta.get('snoozed_until'),
                recurrence_json,
                processed_by_val,
                meta.get('due'),
                meta.get('created'),
                meta.get('last_edited'),
                _is_blocked_val,
                # last_fired / fire_count: check recurrence block first, then top-level
                (meta.get('recurrence') or {}).get('last_fired') or meta.get('last_fired'),
                (meta.get('recurrence') or {}).get('fire_count') or meta.get('fire_count'),
            ]

            all_cols = list(core_cols)
            all_vals = list(core_vals)

            # Grouping-level dimension columns.
            for d in grouping_dims:
                all_cols.append(d)
                all_vals.append(meta.get(d))

            # Schema-derived scalar attribute columns.
            # Use columned_* (not indexed_*) so the column tier (no index) gets
            # populated too — an attribute with `storage: "column"` has a column
            # that must be filled here even though it has no CREATE INDEX.
            # Special-cased values (action_parameters) handled per-attribute.
            _already_written = set(all_cols)
            for _attr in schema.columned_scalar_attrs():
                if _attr in _already_written:
                    continue  # handled as core or grouping dim
                if _attr == "action_parameters":
                    value = _action_params_json
                else:
                    value = meta.get(_attr)
                all_cols.append(_attr)
                all_vals.append(value)

            # Schema-derived list attribute columns (comma-separated).
            _already_written = set(all_cols)
            for _list_attr in schema.columned_list_attrs():
                if _list_attr in _already_written:
                    continue
                raw = meta.get(_list_attr)
                if isinstance(raw, list):
                    csv_val = ",".join(str(v).strip() for v in raw)
                elif raw:
                    csv_val = str(raw).strip()
                else:
                    csv_val = None
                all_cols.append(_list_attr)
                all_vals.append(csv_val)

            placeholders = ", ".join(["?"] * len(all_cols))
            col_str = ", ".join(all_cols)
            c.execute(f"INSERT INTO entities ({col_str}) VALUES ({placeholders})", all_vals)
            entity_count += 1
        except sqlite3.IntegrityError as e:
            errors.append(f"Duplicate entity {meta.get('id')}: {e}")
            continue

        # Extract relationships
        for key in meta:
            if key in schema.relationship_names:
                targets = meta[key]
                if isinstance(targets, list):
                    for target_id in targets:
                        target_id = target_id.strip()
                        if len(target_id) == 36 and '-' in target_id:  # UUID check
                            c.execute("""
                                INSERT OR IGNORE INTO relationships (source_id, relationship, target_id)
                                VALUES (?, ?, ?)
                            """, (meta['id'], key, target_id))
                            rel_count += 1
                elif isinstance(targets, str) and len(targets) == 36 and '-' in targets:
                    c.execute("""
                        INSERT OR IGNORE INTO relationships (source_id, relationship, target_id)
                        VALUES (?, ?, ?)
                    """, (meta['id'], key, targets))
                    rel_count += 1

    conn.commit()

    # Populate FTS5 search index from all live entities
    c.execute("""
        INSERT INTO fts_entities(entity_id, name, description)
        SELECT id, COALESCE(name, ''), COALESCE(description, '')
        FROM entities
        WHERE meta_status = 'live'
    """)
    conn.commit()

    # Print summary
    print(f"Migration complete!")
    print(f"  Entities: {entity_count}")
    print(f"  Relationships: {rel_count}")
    print(f"  (Changelog starts fresh — history accumulates from this point)")
    if errors:
        print(f"  Errors: {len(errors)}")
        for e in errors:
            print(f"    - {e}")

    # Verify
    c.execute("SELECT type, COUNT(*) FROM entities GROUP BY type ORDER BY COUNT(*) DESC")
    print(f"\nEntities by type:")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")

    c.execute("SELECT relationship, COUNT(*) FROM relationships GROUP BY relationship ORDER BY COUNT(*) DESC")
    print(f"\nRelationships by type:")
    for row in c.fetchall():
        print(f"  {row[0]}: {row[1]}")

    # Checkpoint and close the temp DB so the WAL is fully flushed before rename.
    # Must fetchall() to finalize the prepared statement — on Windows, an unfetched
    # result holds the file open via sqlite3_close_v2() deferred cleanup, causing
    # os.replace() to fail with PermissionError (Mac/Linux rename() doesn't care).
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.fetchall()
    c.close()
    conn.close()

    # Remove temp WAL/SHM artifacts from the build process.
    for ext in ("-shm", "-wal"):
        sidecar = tmp_path + ext
        if os.path.exists(sidecar):
            os.remove(sidecar)

    # Remove stale WAL/SHM for the old DB — they'll be orphaned after the rename.
    for ext in ("-shm", "-wal"):
        sidecar = DB_PATH + ext
        if os.path.exists(sidecar):
            os.remove(sidecar)

    # Atomic swap: live DB only changes when the full build is on disk.
    import time as _time
    for attempt in range(10):
        try:
            os.replace(tmp_path, DB_PATH)
            break
        except PermissionError:
            if attempt == 9:
                print(
                    "\nError: substrate.db is locked by another process.\n"
                    "Close any other Substrate processes (entity-watcher, Surface, Relay)\n"
                    "and run:  substrate index rebuild"
                )
                sys.exit(1)
            _time.sleep(1.0)
    print(f"\nDatabase written to: {DB_PATH}")


if __name__ == "__main__":
    main()
