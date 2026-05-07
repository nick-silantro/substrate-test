#!/usr/bin/env python3
"""
Query the Substrate workspace.

Usage:
  python3 query.py pending                    # Unresolved work (focus != Closed, resolution = Unresolved)
  python3 query.py active                     # Actively worked items (focus = Active)
  python3 query.py entity UUID                # Full entity details
  python3 query.py find "search term"                          # Search by name
  python3 query.py find "search term" --type TYPE              # Filter by entity type
  python3 query.py find "Bulletin Board" --path --type context-doc  # Get content file path for a context doc
  python3 query.py type task                  # All entities of a type
  python3 query.py relationships UUID         # All relationships for entity
  python3 query.py stats                      # Workspace overview
  python3 query.py children UUID              # Direct children (contains)
  python3 query.py tree UUID                  # Full hierarchy under entity
  python3 query.py dim DIMENSION [VALUE]      # Query by dimension (e.g. dim focus Active)
  python3 query.py workable                   # Items available for work (not is_blocked, no blocked ancestors)
  python3 query.py stuck                      # Permanently stuck items (is_blocked + depend on Cancelled/Deferred)
  python3 query.py by ACTOR                   # Work performed by or owned by an actor (UUID, short UUID, or handle)
  python3 query.py unprocessed AGENT [TYPE]   # Entities not yet processed by AGENT (optionally filter by type)
  python3 query.py search "query text"        # Semantic search by meaning (requires setup-search.py)
  python3 query.py changelog                  # Last 20 changes
  python3 query.py changelog UUID             # History of a specific entity
  python3 query.py changelog --agent alpha    # Changes by a specific agent
  python3 query.py changelog --since DATE     # Changes since date (ISO format)
  python3 query.py changelog --last N         # Last N entries (default: 20)
  python3 query.py changelog --op create       # Only create operations (create/update/delete/cascade)
  python3 query.py changelog --all            # All entries
  python3 query.py due                         # Overdue items (next_due <= today)
  python3 query.py due 7                       # Due within 7 days
  python3 query.py due --type chore            # Only chores
  python3 query.py chores                      # All chores with status + streak + next_due
  python3 query.py chores --due                # Only due/overdue chores
  python3 query.py triggers                    # All active trigger entities
  python3 query.py trigger-history             # Recent trigger firings from changelog
  python3 query.py trigger-history UUID        # Firings for a specific entity
  python3 query.py completion-history UUID     # Completion history for recurring entity
  python3 query.py theme                       # List all themes with entity counts
  python3 query.py theme NAME                  # All work entities with that theme
"""

import os
import sys
import json
import sqlite3
from datetime import date, timedelta

SUBSTRATE_PATH = os.environ.get("SUBSTRATE_PATH", os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DB_PATH = os.path.join(SUBSTRATE_PATH, "_system", "index", "substrate.db")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from schema import SubstrateSchema
from lib.db import open_db

def _get_schema():
    from schema import load_schema
    return load_schema(SUBSTRATE_PATH)


def get_conn():
    conn = open_db(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def format_dims(row_dict):
    """Format dimensional status into a compact string."""
    parts = []
    # FLAIR
    for dim, label in [("focus", "F"), ("life_stage", "LS"), ("resolution", "R"),
                       ("importance_tactical", "IT"), ("assessment", "A")]:
        val = row_dict.get(dim)
        if val:
            parts.append(f"{label}:{val}")
    # HIP
    for dim, label in [("health", "H"), ("phase", "P"), ("importance_strategic", "IS")]:
        val = row_dict.get(dim)
        if val:
            parts.append(f"{label}:{val}")
    return " | ".join(parts) if parts else "-"


def cmd_pending(args):
    """Show unresolved work items."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, type, focus, life_stage, resolution, importance_tactical
        FROM entities
        WHERE resolution = 'unresolved'
        AND focus NOT IN ('closed', 'paused')
        AND meta_status = 'live'
        ORDER BY
            CASE WHEN importance_tactical = 'critical' THEN 0
                 WHEN importance_tactical = 'high' THEN 1
                 WHEN importance_tactical = 'medium' THEN 2
                 WHEN importance_tactical = 'low' THEN 3
                 ELSE 4 END,
            CASE WHEN focus = 'active' THEN 0
                 WHEN focus = 'waiting' THEN 1
                 ELSE 2 END,
            CASE WHEN is_blocked = 'true' THEN 1 ELSE 0 END,
            type, name
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No pending work found.")
        return

    print(f"{'Type':<12} {'Focus':<10} {'Life Stage':<14} {'Priority':<10} {'Name':<40} {'ID'}")
    print("-" * 130)
    for eid, name, etype, focus, life_stage, resolution, imp_tac in rows:
        print(f"{etype:<12} {(focus or '-'):<10} {(life_stage or '-'):<14} {(imp_tac or '-'):<10} {(name or '-')[:40]:<40} {eid}")


def cmd_active(args):
    """Show items being actively worked on."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT id, name, type, focus, life_stage, importance_tactical
        FROM entities
        WHERE focus = 'active'
        AND meta_status = 'live'
        ORDER BY
            CASE WHEN importance_tactical = 'critical' THEN 0
                 WHEN importance_tactical = 'high' THEN 1
                 WHEN importance_tactical = 'medium' THEN 2
                 WHEN importance_tactical = 'low' THEN 3
                 ELSE 4 END,
            type, name
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No active work items.")
        return

    print(f"{'Type':<12} {'Life Stage':<14} {'Priority':<10} {'Name':<40} {'ID'}")
    print("-" * 120)
    for eid, name, etype, focus, life_stage, imp_tac in rows:
        print(f"{etype:<12} {(life_stage or '-'):<14} {(imp_tac or '-'):<10} {(name or '-')[:40]:<40} {eid}")


def cmd_entity(args):
    """Show full details for an entity."""
    if not args:
        print("Usage: query.py entity UUID")
        return

    entity_id = args[0]
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT * FROM entities WHERE id = ? OR id LIKE ?", (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    if not row:
        print(f"Entity not found: {entity_id}")
        return

    cols = [d[0] for d in c.description]
    entity = dict(zip(cols, row))

    print(f"{'='*60}")
    print(f"  {entity['name']}")
    print(f"  Type: {entity['type']}  |  Meta: {entity['meta_status']}")
    print(f"  ID: {entity['id']}")
    print(f"  Path: {entity['path']}")
    if entity.get('description'):
        print(f"  Description: {entity['description']}")

    # Dimensional status
    dims = format_dims(entity)
    if dims != "-":
        print(f"  Dimensions: {dims}")

    # Assignment attributes (performed_by and assigned_to are now relationships — see relationships table)
    if entity.get('processed_by'):
        print(f"  Processed by: {entity['processed_by']}")

    if entity.get('due'):
        print(f"  Due: {entity['due']}")
    print(f"  Created: {entity.get('created', '-')}  |  Last Edited: {entity.get('last_edited', '-')}")

    # Relationships
    c.execute("""
        SELECT 'outgoing' as dir, relationship, target_id, e.name, e.type
        FROM relationships r LEFT JOIN entities e ON r.target_id = e.id
        WHERE r.source_id = ?
        UNION ALL
        SELECT 'incoming' as dir, relationship, source_id, e.name, e.type
        FROM relationships r LEFT JOIN entities e ON r.source_id = e.id
        WHERE r.target_id = ?
        ORDER BY dir, relationship
    """, (entity['id'], entity['id']))

    rels = c.fetchall()
    if rels:
        print(f"\n  Relationships:")
        for direction, rel, other_id, other_name, other_type in rels:
            arrow = "→" if direction == "outgoing" else "←"
            print(f"    {arrow} {rel} {other_name or '?'} ({other_type or '?'}) [{other_id}]")

    conn.close()
    print(f"{'='*60}")


def cmd_find(args):
    """Search entities by name."""
    if not args:
        print("Usage: query.py find 'search term' [--path] [--type TYPE]")
        return

    path_only = "--path" in args
    args = [a for a in args if a != "--path"]

    type_filter = None
    if "--type" in args:
        idx = args.index("--type")
        if idx + 1 < len(args):
            type_filter = args[idx + 1]
            args = args[:idx] + args[idx + 2:]

    term = " ".join(args)

    conn = get_conn()
    c = conn.cursor()
    if type_filter:
        c.execute("""SELECT id, name, type, focus, resolution, phase, path
                     FROM entities WHERE name LIKE ? AND type = ? AND meta_status = 'live' ORDER BY type, name""",
                  (f"%{term}%", type_filter))
    else:
        c.execute("""SELECT id, name, type, focus, resolution, phase, path
                     FROM entities WHERE name LIKE ? AND meta_status = 'live' ORDER BY type, name""",
                  (f"%{term}%",))
    rows = c.fetchall()
    conn.close()

    if not rows:
        print(f"No entities matching '{term}'" + (f" of type '{type_filter}'" if type_filter else ""))
        return

    if path_only:
        for eid, name, etype, focus, resolution, phase, epath in rows:
            if epath:
                entity_dir = os.path.join(SUBSTRATE_PATH, epath)
                if os.path.isdir(entity_dir):
                    content_files = sorted([
                        f for f in os.listdir(entity_dir)
                        if os.path.isfile(os.path.join(entity_dir, f)) and f != "meta.yaml"
                    ])
                    for cf in content_files:
                        print(os.path.join(entity_dir, cf))
        return

    for eid, name, etype, focus, resolution, phase, epath in rows:
        dim_str = focus or phase or "-"
        if resolution and resolution != "unresolved":
            dim_str = resolution
        print(f"  [{etype}] {name} ({dim_str}) — {eid}")


def cmd_type(args):
    """List all entities of a type."""
    if not args:
        print("Usage: query.py type TYPE")
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute("""SELECT id, name, focus, life_stage, resolution, phase, importance_tactical, health
                 FROM entities WHERE type = ? AND meta_status = 'live' ORDER BY name""",
              (args[0],))
    rows = c.fetchall()
    conn.close()

    if not rows:
        print(f"No {args[0]} entities found.")
        return

    print(f"{args[0]} entities ({len(rows)}):")
    for eid, name, focus, life_stage, resolution, phase, imp_tac, health in rows:
        row_dict = {"focus": focus, "life_stage": life_stage, "resolution": resolution,
                    "phase": phase, "importance_tactical": imp_tac, "health": health}
        dims = format_dims(row_dict)
        print(f"  {dims:<40} {name} [{eid}]")


def cmd_relationships(args):
    """Show all relationships for an entity."""
    if not args:
        print("Usage: query.py relationships UUID")
        return
    cmd_entity(args)


def cmd_stats(args):
    """Workspace overview."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM entities WHERE meta_status = 'live'")
    total = c.fetchone()[0]

    c.execute("SELECT type, COUNT(*) FROM entities WHERE meta_status = 'live' GROUP BY type ORDER BY COUNT(*) DESC")
    by_type = c.fetchall()

    c.execute("SELECT COUNT(*) FROM relationships")
    rel_count = c.fetchone()[0]

    # Dimensional work summary
    c.execute("""SELECT COUNT(*) FROM entities
                 WHERE resolution = 'unresolved' AND focus NOT IN ('closed', 'paused')
                 AND meta_status = 'live'""")
    pending = c.fetchone()[0]

    c.execute("""SELECT COUNT(*) FROM entities
                 WHERE focus = 'active' AND meta_status = 'live'""")
    active = c.fetchone()[0]

    c.execute("""SELECT COUNT(*) FROM entities
                 WHERE is_blocked = 'true' AND meta_status = 'live'""")
    blocked = c.fetchone()[0]

    conn.close()

    print(f"Substrate Workspace Stats")
    print(f"{'='*40}")
    print(f"  Total entities: {total}")
    print(f"  Pending work: {pending}")
    print(f"  Active now: {active}")
    print(f"  Blocked: {blocked}")
    print(f"  Relationships: {rel_count}")
    print(f"\n  By type:")
    for t, count in by_type:
        print(f"    {t:<16} {count}")


def cmd_children(args):
    """Direct children of an entity."""
    if not args:
        print("Usage: query.py children UUID")
        return

    # Downward traversal: use parent-side (inverse) relationship names
    schema = _get_schema()
    containment_rels = sorted(schema.inverse_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))

    conn = get_conn()
    c = conn.cursor()
    c.execute(f"""
        SELECT e.id, e.name, e.type, e.focus, e.resolution, e.phase, e.life_stage
        FROM relationships r JOIN entities e ON r.target_id = e.id
        WHERE r.source_id LIKE ? AND r.relationship IN ({placeholders})
          AND e.meta_status = 'live'
        ORDER BY e.type, e.name
    """, [f"{args[0]}%"] + containment_rels)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No children found.")
        return

    for eid, name, etype, focus, resolution, phase, life_stage in rows:
        dim_str = life_stage or focus or phase or "-"
        if life_stage == 'done_working' and resolution and resolution != "unresolved":
            dim_str = f"{life_stage}/{resolution}"
        print(f"  [{etype}] {name} ({dim_str}) — {eid}")


def cmd_tree(args):
    """Recursive hierarchy under an entity."""
    if not args:
        print("Usage: query.py tree UUID")
        return

    # Downward traversal: use parent-side (inverse) relationship names
    schema = _get_schema()
    containment_rels = sorted(schema.inverse_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))

    conn = get_conn()

    def print_tree(entity_id, depth=0):
        c = conn.cursor()
        c.execute("SELECT id, name, type, focus, resolution, phase, life_stage FROM entities WHERE id LIKE ?", (f"{entity_id}%",))
        row = c.fetchone()
        if not row:
            return
        eid, name, etype, focus, resolution, phase, life_stage = row
        dim_str = life_stage or focus or phase or "-"
        if life_stage == 'done_working' and resolution and resolution != "unresolved":
            dim_str = f"{life_stage}/{resolution}"
        prefix = "  " * depth + ("└─ " if depth > 0 else "")
        print(f"{prefix}[{etype}] {name} ({dim_str})")

        c.execute(f"""
            SELECT r.target_id FROM relationships r
            JOIN entities e ON r.target_id = e.id
            WHERE r.source_id = ? AND r.relationship IN ({placeholders})
              AND e.meta_status = 'live'
        """, [eid] + containment_rels)
        children = c.fetchall()
        for (child_id,) in children:
            print_tree(child_id, depth + 1)

    print_tree(args[0])
    conn.close()


def cmd_dim(args):
    """Query by dimension."""
    if not args:
        print("Usage: query.py dim DIMENSION [VALUE]")
        print("  e.g. query.py dim focus Active")
        print("       query.py dim resolution Completed")
        print("       query.py dim phase")
        return

    dimension = args[0]
    value = args[1] if len(args) > 1 else None

    schema = _get_schema()
    valid_dims = schema.dimension_names  # HIP/FLAIR + meta_status + grouping-level dims
    if dimension not in valid_dims:
        print(f"Unknown dimension: {dimension}")
        print(f"Valid dimensions: {', '.join(sorted(valid_dims))}")
        return

    conn = get_conn()
    c = conn.cursor()

    if value:
        c.execute(f"""SELECT id, name, type, {dimension}
                      FROM entities WHERE {dimension} = ? AND meta_status = 'live'
                      ORDER BY type, name""", (value,))
    else:
        c.execute(f"""SELECT {dimension}, COUNT(*) FROM entities
                      WHERE {dimension} IS NOT NULL AND meta_status = 'live'
                      GROUP BY {dimension} ORDER BY COUNT(*) DESC""")
        print(f"Distribution of {dimension}:")
        for val, count in c.fetchall():
            print(f"  {val:<20} {count}")
        conn.close()
        return

    rows = c.fetchall()
    conn.close()

    if not rows:
        print(f"No entities with {dimension} = {value}")
        return

    print(f"{dimension} = {value} ({len(rows)} entities):")
    for eid, name, etype, dim_val in rows:
        print(f"  [{etype}] {name} [{eid}]")


def _has_blocked_ancestor(entity_id, conn, blocked_ids, cache=None):
    """Check if any ancestor (via containment relationships) has is_blocked=true."""
    if cache is None:
        cache = {}
    if entity_id in cache:
        return cache[entity_id]

    # Upward traversal: use child-side (forward) relationship names
    schema = _get_schema()
    containment_rels = sorted(schema.forward_relationships_by_category['containment'])
    placeholders = ','.join('?' * len(containment_rels))

    c = conn.cursor()
    c.execute(
        f"SELECT target_id FROM relationships "
        f"WHERE source_id = ? AND relationship IN ({placeholders})",
        [entity_id] + containment_rels,
    )
    parents = [row[0] for row in c.fetchall()]

    for parent_id in parents:
        if parent_id in blocked_ids:
            cache[entity_id] = True
            return True
        if _has_blocked_ancestor(parent_id, conn, blocked_ids, cache):
            cache[entity_id] = True
            return True

    cache[entity_id] = False
    return False


def cmd_workable(args):
    """Show work that can actually be picked up right now.

    Like pending, but filters out:
    - Entities with is_blocked = true (directly blocked by dependencies)
    - Entities whose ancestors (ticket, project, etc.) are blocked
    """
    conn = get_conn()
    c = conn.cursor()

    # Get all blocked entity IDs for ancestry check
    c.execute("SELECT id FROM entities WHERE is_blocked = 'true' AND meta_status = 'live'")
    blocked_ids = {row[0] for row in c.fetchall()}

    # Only work-nature types have life_stage — object-only types (horizons, planning, utility)
    # are excluded by requiring life_stage to be non-null.
    c.execute("""
        SELECT id, name, type, focus, life_stage, resolution, importance_tactical
        FROM entities
        WHERE resolution = 'unresolved'
        AND life_stage IS NOT NULL
        AND focus NOT IN ('closed', 'paused')
        AND (is_blocked IS NULL OR is_blocked != 'true')
        AND meta_status = 'live'
        ORDER BY
            CASE WHEN importance_tactical = 'critical' THEN 0
                 WHEN importance_tactical = 'high' THEN 1
                 WHEN importance_tactical = 'medium' THEN 2
                 WHEN importance_tactical = 'low' THEN 3
                 ELSE 4 END,
            CASE WHEN focus = 'active' THEN 0
                 WHEN focus = 'waiting' THEN 1
                 ELSE 2 END,
            type, name
    """)
    rows = c.fetchall()

    # Filter out entities with Blocked ancestors
    cache = {}
    filtered = []
    for row in rows:
        eid = row[0]
        if not _has_blocked_ancestor(eid, conn, blocked_ids, cache):
            filtered.append(row)

    conn.close()

    if not filtered:
        print("No workable items found.")
        return

    print(f"{'Type':<12} {'Focus':<10} {'Life Stage':<14} {'Priority':<10} {'Name':<40} {'ID'}")
    print("-" * 130)
    for eid, name, etype, focus, life_stage, resolution, imp_tac in filtered:
        print(f"{etype:<12} {(focus or '-'):<10} {(life_stage or '-'):<14} {(imp_tac or '-'):<10} {(name or '-')[:40]:<40} {eid}")


def cmd_stuck(args):
    """Show entities that are permanently stuck.

    Entities with is_blocked=true where at least one depends_on target has
    resolution in (Cancelled, Deferred) — meaning the dependency will never
    resolve on its own. Requires human/L1 decision.
    """
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT e.id, e.name, e.type, e.is_blocked, e.life_stage,
               dep.id as dep_id, dep.name as dep_name, dep.type as dep_type, dep.resolution as dep_resolution
        FROM entities e
        JOIN relationships r ON e.id = r.source_id AND r.relationship = 'depends_on'
        JOIN entities dep ON r.target_id = dep.id
        WHERE e.is_blocked = 'true'
        AND e.resolution = 'unresolved'
        AND e.meta_status = 'live'
        AND dep.resolution IN ('cancelled', 'deferred')
        ORDER BY e.type, e.name
    """)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No stuck entities found.")
        return

    print("Stuck entities (blocked by cancelled/deferred dependencies):")
    print("-" * 120)
    current_entity = None
    for eid, name, etype, is_blocked, life_stage, dep_id, dep_name, dep_type, dep_res in rows:
        if eid != current_entity:
            current_entity = eid
            print(f"  [{etype}] {name} ({life_stage or '-'}) [{eid}]")
        print(f"    blocked by: [{dep_type}] {dep_name} ({dep_res}) [{dep_id}]")


def _resolve_actor(actor_ref, conn):
    """Resolve an actor reference (UUID, short UUID, or handle) to a full UUID.

    Returns (uuid, name, type) or None.
    """
    c = conn.cursor()
    # Try exact UUID match
    c.execute("SELECT id, name, type FROM entities WHERE id = ?", (actor_ref,))
    row = c.fetchone()
    if row:
        return row
    # Try short UUID prefix
    c.execute("SELECT id, name, type FROM entities WHERE id LIKE ?", (f"{actor_ref}%",))
    rows = c.fetchall()
    if len(rows) == 1:
        return rows[0]
    if len(rows) > 1:
        print(f"Ambiguous UUID prefix '{actor_ref}' — matches {len(rows)} entities")
        return None
    # Try handle (user and agent entities) — case-insensitive; handle is the stable identity
    c.execute("SELECT id, name, type FROM entities WHERE handle IS NOT NULL AND LOWER(handle) = LOWER(?)", (actor_ref,))
    row = c.fetchone()
    if row:
        return row
    # Try name match (fallback for entities without handle) — case-insensitive
    c.execute("SELECT id, name, type FROM entities WHERE type IN ('user', 'agent') AND LOWER(name) = LOWER(?)", (actor_ref,))
    row = c.fetchone()
    if row:
        return row
    return None


def cmd_by(args):
    """Show work performed by or owned by an actor."""
    if not args:
        print("Usage: query.py by ACTOR  (UUID, short UUID, or handle)")
        return

    conn = get_conn()
    actor = _resolve_actor(args[0], conn)

    if not actor:
        print(f"Actor not found: {args[0]}")
        conn.close()
        return

    actor_id, actor_name, actor_type = actor
    c = conn.cursor()

    # Tasks performed by this actor (performed_by relationship)
    c.execute("""
        SELECT DISTINCT e.id, e.name, e.type, e.focus, e.life_stage, e.resolution, e.importance_tactical
        FROM entities e
        JOIN relationships r ON r.source_id = e.id
        WHERE r.relationship = 'performed_by' AND r.target_id = ? AND e.meta_status = 'live'
        ORDER BY
            CASE WHEN e.resolution = 'unresolved' THEN 0 ELSE 1 END,
            e.type, e.name
    """, (actor_id,))
    performed = c.fetchall()

    # Work assigned to this actor (assigned_to relationship)
    c.execute("""
        SELECT DISTINCT e.id, e.name, e.type, e.focus, e.life_stage, e.resolution, e.importance_tactical
        FROM entities e
        JOIN relationships r ON r.source_id = e.id
        WHERE r.relationship = 'assigned_to' AND r.target_id = ? AND e.meta_status = 'live'
        ORDER BY
            CASE WHEN e.resolution = 'unresolved' THEN 0 ELSE 1 END,
            e.type, e.name
    """, (actor_id,))
    owned = c.fetchall()

    conn.close()

    print(f"Work for {actor_name} ({actor_type}) [{actor_id[:8]}]")
    print(f"{'='*80}")

    if performed:
        print(f"\nPerformed by ({len(performed)}):")
        print(f"  {'Type':<12} {'Focus':<10} {'Life Stage':<14} {'Resolution':<12} {'Name':<30}")
        print(f"  {'-'*78}")
        for eid, name, etype, focus, ls, res, it in performed:
            print(f"  {etype:<12} {(focus or '-'):<10} {(ls or '-'):<14} {(res or '-'):<12} {(name or '-')[:30]:<30}")
    else:
        print("\nPerformed by: (none)")

    if owned:
        print(f"\nOwns ({len(owned)}):")
        print(f"  {'Type':<12} {'Focus':<10} {'Life Stage':<14} {'Resolution':<12} {'Name':<30}")
        print(f"  {'-'*78}")
        for eid, name, etype, focus, ls, res, it in owned:
            print(f"  {etype:<12} {(focus or '-'):<10} {(ls or '-'):<14} {(res or '-'):<12} {(name or '-')[:30]:<30}")
    else:
        print("\nOwns: (none)")


def cmd_unprocessed(args):
    """Show entities not yet processed by a given agent."""
    if not args:
        print("Usage: query.py unprocessed AGENT [TYPE]")
        print("  e.g. query.py unprocessed carl-compress")
        print("       query.py unprocessed carl-compress diary-entry")
        return

    agent = args[0]
    entity_type = args[1] if len(args) > 1 else None

    conn = get_conn()
    c = conn.cursor()

    sql = """
        SELECT id, name, type, focus, life_stage, resolution
        FROM entities
        WHERE meta_status = 'live'
        AND (processed_by IS NULL OR processed_by NOT LIKE ?)
    """
    params = [f"%{agent}%"]

    if entity_type:
        sql += " AND type = ?"
        params.append(entity_type)

    sql += " ORDER BY type, name"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        label = f" of type {entity_type}" if entity_type else ""
        print(f"No unprocessed entities{label} for {agent}.")
        return

    label = f" (type: {entity_type})" if entity_type else ""
    print(f"Not processed by {agent}{label} ({len(rows)} entities):")
    for eid, name, etype, focus, ls, res in rows:
        dim_str = focus or res or "-"
        if res and res != "unresolved":
            dim_str = res
        print(f"  [{etype}] {name} ({dim_str}) — {eid}")


def cmd_search(args):
    """Hybrid search — FTS5 keyword + semantic vector, blended via RRF."""
    if not args:
        print("Usage: query.py search \"query text\" [--type TYPE] [--limit N] [--format json]")
        print("  e.g. query.py search \"things I need to decide on\"")
        print("       query.py search \"trading intelligence\" --type project")
        print("       query.py search \"agent coordination\" --format json")
        return

    # Parse args: query text, optional --type, --limit, --format
    query_parts = []
    type_filter = None
    limit = 10
    output_format = "human"
    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            type_filter = args[i + 1]
            i += 2
        elif args[i] == "--limit" and i + 1 < len(args):
            try:
                limit = int(args[i + 1])
            except ValueError:
                print(f"Invalid --limit value: {args[i + 1]}")
                return
            i += 2
        elif args[i] == "--format" and i + 1 < len(args):
            output_format = args[i + 1]
            if output_format not in ("human", "json"):
                print(f"Invalid --format value: {output_format}. Use 'human' or 'json'.")
                return
            i += 2
        else:
            query_parts.append(args[i])
            i += 1

    query_text = " ".join(query_parts)
    if not query_text:
        print("No query text provided.")
        return

    from embeddings import is_search_available, hybrid_search, load_vec_extension

    if not is_search_available() and output_format == "human":
        print("Note: semantic search not set up — showing keyword results only.")
        print("      Run `substrate search setup` to enable semantic matching.\n")

    conn = get_conn()
    load_vec_extension(conn)  # loads sqlite-vec if available; no-op otherwise

    results = hybrid_search(conn, query_text, limit=limit, type_filter=type_filter)
    conn.close()

    if not results:
        if output_format == "json":
            import json
            print(json.dumps({"query": query_text, "type_filter": type_filter, "results": []}))
        else:
            print(f'No results for: "{query_text}"')
            if type_filter:
                print(f"  (filtered to type: {type_filter})")
        return

    if output_format == "json":
        import json
        out = {
            "query": query_text,
            "type_filter": type_filter,
            "results": [
                {
                    "id": r["id"],
                    "type": r["type"],
                    "name": r["name"],
                    "description": r["description"] if r["description"] != "[awaiting context]" else None,
                    "score": r["score"],
                }
                for r in results
            ],
        }
        print(json.dumps(out, indent=2))
        return

    filter_label = f" (type: {type_filter})" if type_filter else ""
    print(f'Search: "{query_text}"{filter_label}')
    print(f"{'='*80}")
    _MAX_RRF_SCORE = 2.0 / (60 + 1)  # both sources at rank 1: 1/(60+1) + 1/(60+1)
    for i, r in enumerate(results, 1):
        bar_width = min(20, int(r["score"] / _MAX_RRF_SCORE * 20))
        bar = "█" * bar_width
        desc = (r["description"] or "")[:60]
        if desc == "[awaiting context]":
            desc = ""
        print(f"  {i:>2}. [{r['type']}] {r['name']}")
        if desc:
            print(f"      {desc}")
        print(f"      {bar}  —  {r['id']}")


def cmd_changelog(args):
    """Show change history from the CDC log.

    Uses the SQLite changelog index for fast querying. Falls back to
    scanning JSONL files if the index doesn't exist yet.
    """
    import json
    from changelog import ensure_changelog_table

    # Parse arguments
    entity_filter = None
    agent_filter = None
    since_filter = None
    operation_filter = None
    limit = 20
    i = 0
    while i < len(args):
        if args[i] == "--agent" and i + 1 < len(args):
            agent_filter = args[i + 1]
            i += 2
        elif args[i] == "--since" and i + 1 < len(args):
            since_filter = args[i + 1]
            i += 2
        elif args[i] == "--last" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--op" and i + 1 < len(args):
            operation_filter = args[i + 1]
            i += 2
        elif args[i] == "--all":
            limit = None
            i += 1
        elif not args[i].startswith("--"):
            entity_filter = args[i]
            i += 1
        else:
            print(f"Unknown option: {args[i]}")
            sys.exit(1)

    conn = get_conn()

    # Check if changelog table exists
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='changelog'")
    has_table = c.fetchone() is not None

    if not has_table:
        # Try to create and populate from JSONL
        ensure_changelog_table(conn)
        # Check if we have any JSONL files to backfill from
        from changelog import all_log_files
        log_files = all_log_files()
        if log_files:
            print("Building changelog index from JSONL files...")
            count = 0
            for log_path in log_files:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                            conn.execute("""
                                INSERT INTO changelog (timestamp, operation, entity_id, entity_type,
                                                       entity_name, agent, triggered_by, raw_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                entry["timestamp"],
                                entry["operation"],
                                entry["entity_id"],
                                entry["entity_type"],
                                entry.get("entity_name"),
                                entry.get("agent"),
                                entry.get("triggered_by"),
                                line,
                            ))
                            count += 1
                        except (json.JSONDecodeError, KeyError):
                            pass
            conn.commit()
            print(f"  Indexed {count} entries.\n")
        else:
            print("No change log found. Changes will be recorded after the next create or update operation.")
            conn.close()
            return

    # Build query
    sql = "SELECT raw_json FROM changelog WHERE 1=1"
    params = []

    if entity_filter:
        # Support both full UUID and prefix
        if len(entity_filter) == 36:
            sql += " AND entity_id = ?"
            params.append(entity_filter)
        else:
            sql += " AND entity_id LIKE ?"
            params.append(f"{entity_filter}%")

    if agent_filter:
        sql += " AND agent = ?"
        params.append(agent_filter)

    if since_filter:
        sql += " AND timestamp >= ?"
        params.append(since_filter)

    if operation_filter:
        sql += " AND operation = ?"
        params.append(operation_filter)

    sql += " ORDER BY rowid"

    if limit:
        # Get total count first, then fetch last N
        count_sql = sql.replace("SELECT raw_json", "SELECT COUNT(*)", 1)
        c.execute(count_sql, params)
        total = c.fetchone()[0]
        if total > limit:
            sql += f" LIMIT {limit} OFFSET {total - limit}"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No matching changelog entries.")
        return

    # Display
    for (raw_json,) in rows:
        entry = json.loads(raw_json)
        ts = entry["timestamp"]
        op = entry["operation"].upper()
        etype = entry["entity_type"]
        ename = entry["entity_name"]
        eid = entry["entity_id"][:8]
        agent = entry.get("agent", "-")

        header = f"[{ts}] {op} {etype} \"{ename}\" [{eid}]"
        if agent != "-":
            header += f"  (agent: {agent})"
        if entry.get("triggered_by"):
            header += f"  triggered by [{entry['triggered_by'][:8]}]"
        print(header)

        for ch in (entry.get("changes") or []):
            if "old" in ch:
                print(f"   {ch['attribute']}: {ch['old']} → {ch['new']}")
            else:
                print(f"   {ch['attribute']}: {ch['value']}")

        for rel in (entry.get("relationships") or []):
            action = rel["action"]
            tname = rel.get("target_name") or rel["target_id"][:8]
            if action == "change":
                print(f"   rel: {rel['old_type']} → {rel['new_type']} for {tname}")
            else:
                print(f"   rel: {action} {rel['type']} → {tname}")

    print(f"\n({len(rows)} entries)")


def cmd_due(args):
    """Show items that are due or overdue.

    Usage:
      query.py due                  # Overdue (next_due <= today)
      query.py due 7                # Due within 7 days
      query.py due --type chore     # Only chores
    """
    within_days = None
    type_filter = None

    i = 0
    while i < len(args):
        if args[i] == "--type" and i + 1 < len(args):
            type_filter = args[i + 1]
            i += 2
        else:
            try:
                within_days = int(args[i])
            except ValueError:
                print(f"Unknown argument: {args[i]}")
                return
            i += 1

    today = date.today()
    if within_days is not None:
        cutoff = (today + timedelta(days=within_days)).isoformat()
    else:
        cutoff = today.isoformat()

    conn = get_conn()
    c = conn.cursor()

    sql = """
        SELECT id, name, type, next_due, focus, life_stage, streak
        FROM entities
        WHERE next_due <= ? AND resolution = 'unresolved' AND meta_status = 'live'
    """
    params = [cutoff]

    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)

    sql += " ORDER BY next_due, name"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        label = f"within {within_days} days" if within_days else "overdue"
        if type_filter:
            label += f" (type: {type_filter})"
        print(f"No items {label}.")
        return

    header = "Due" if within_days else "Overdue"
    if within_days:
        header += f" within {within_days} days"
    print(f"{header} ({len(rows)} items):")
    print(f"{'Type':<10} {'Due':<24} {'Focus':<10} {'Life Stage':<14} {'Streak':<7} {'Name':<30} {'ID'}")
    print("-" * 130)
    for eid, name, etype, next_due, focus, life_stage, streak in rows:
        days_until = (date.fromisoformat(next_due) - today).days
        due_label = next_due
        if days_until < 0:
            due_label += f" ({-days_until}d late)"
        elif days_until == 0:
            due_label += " (today)"
        print(f"{etype:<10} {due_label:<24} {(focus or '-'):<10} {(life_stage or '-'):<14} {(streak or 0):<7} {(name or '-')[:30]:<30} {eid}")


def cmd_chores(args):
    """Show all chores with status, streak, and next_due.

    Usage:
      query.py chores            # All chores
      query.py chores --due      # Only due/overdue chores
    """
    due_only = "--due" in args

    conn = get_conn()
    c = conn.cursor()

    sql = """
        SELECT id, name, focus, life_stage, resolution, next_due,
               streak, completion_count, last_completed
        FROM entities
        WHERE type = 'chore' AND meta_status = 'live'
    """
    params = []

    if due_only:
        sql += " AND next_due IS NOT NULL AND next_due <= ?"
        params.append(date.today().isoformat())

    sql += " ORDER BY next_due, name"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No chores found." if not due_only else "No due chores.")
        return

    label = "Due chores" if due_only else "All chores"
    print(f"{label} ({len(rows)}):")
    print(f"{'Focus':<10} {'Resolution':<14} {'Next Due':<24} {'Streak':<7} {'Done':<6} {'Name':<35} {'ID'}")
    print("-" * 132)
    today = date.today()
    for eid, name, focus, life_stage, resolution, next_due, streak, count, last_completed in rows:
        due_str = next_due or "-"
        if next_due:
            days_until = (date.fromisoformat(next_due) - today).days
            if days_until < 0:
                due_str += f" ({-days_until}d late)"
            elif days_until == 0:
                due_str += " (today)"
        print(f"{(focus or '-'):<10} {(resolution or '-'):<14} {due_str:<24} {(streak or 0):<7} {(count or 0):<6} {(name or '-')[:35]:<35} {eid}")


def cmd_triggers(args):
    """Show all active trigger entities.

    Usage:
      query.py triggers          # All active triggers
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT e.id, e.name, e.event_type, e.action_type, e.executor,
               e.condition,
               GROUP_CONCAT(CASE WHEN r.relationship = 'watches' THEN t.name END) as watches,
               GROUP_CONCAT(CASE WHEN r.relationship = 'acts_on' THEN t.name END) as acts_on
        FROM entities e
        LEFT JOIN relationships r ON e.id = r.source_id AND r.relationship IN ('watches', 'acts_on')
        LEFT JOIN entities t ON r.target_id = t.id
        WHERE e.type = 'trigger' AND e.meta_status = 'live'
        AND COALESCE(e.resolution, 'unresolved') = 'unresolved'
        GROUP BY e.id
        ORDER BY e.name
    """)
    rows = c.fetchall()
    conn.close()

    # Also show built-in triggers
    print("Trigger Registry:")
    print("\n  Built-in triggers:")
    print("    builtin:completion_unblock  — Unblock dependents when entity completes")
    print("    builtin:dependency_block    — Block entity when added dependency is unresolved")
    print("    builtin:recurrence_reset    — Reset recurring entity on completion")

    if rows:
        print(f"\n  Entity triggers ({len(rows)}):")
        for eid, name, event_type, action_type, executor, condition, watches, acts_on in rows:
            print(f"    {name} [{eid[:8]}]")
            if event_type and action_type:
                cond_str = f"  when: {condition}" if condition else ""
                print(f"      event: {event_type} → {action_type} (executor: {executor or 'cascade'}){cond_str}")
            if watches:
                print(f"      watches: {watches}")
            if acts_on:
                print(f"      acts_on: {acts_on}")
    else:
        print("\n  No entity triggers defined.")


def cmd_trigger_history(args):
    """Show recent trigger firings from the changelog.

    Usage:
      query.py trigger-history           # All cascade events (last 20)
      query.py trigger-history UUID      # Cascade events for specific entity
    """
    entity_filter = args[0] if args else None

    conn = get_conn()
    c = conn.cursor()

    # Ensure changelog table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='changelog'")
    if not c.fetchone():
        print("No changelog table found. Run an entity operation first.")
        conn.close()
        return

    sql = "SELECT raw_json FROM changelog WHERE operation = 'cascade'"
    params = []

    if entity_filter:
        if len(entity_filter) == 36:
            sql += " AND entity_id = ?"
            params.append(entity_filter)
        else:
            sql += " AND entity_id LIKE ?"
            params.append(f"{entity_filter}%")

    sql += " ORDER BY rowid"

    # Get last 20
    count_sql = sql.replace("SELECT raw_json", "SELECT COUNT(*)", 1)
    c.execute(count_sql, params)
    total = c.fetchone()[0]
    if total > 20:
        sql += f" LIMIT 20 OFFSET {total - 20}"

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print("No trigger/cascade events found.")
        return

    print(f"Trigger history ({len(rows)} of {total} events):")
    for (raw_json,) in rows:
        entry = json.loads(raw_json)
        ts = entry["timestamp"]
        etype = entry["entity_type"]
        ename = entry["entity_name"]
        eid = entry["entity_id"][:8]

        header = f"  [{ts}] {etype} \"{ename}\" [{eid}]"
        if entry.get("triggered_by"):
            header += f"  triggered by [{entry['triggered_by'][:8]}]"
        print(header)

        for ch in (entry.get("changes") or []):
            if "old" in ch:
                print(f"     {ch['attribute']}: {ch['old']} → {ch['new']}")
            else:
                print(f"     {ch['attribute']}: {ch['value']}")


def cmd_completion_history(args):
    """Show completion history for a recurring entity.

    Usage:
      query.py completion-history UUID   # All completions + resets for entity
    """
    if not args:
        print("Usage: query.py completion-history UUID")
        return

    entity_id = args[0]

    conn = get_conn()
    c = conn.cursor()

    # Ensure changelog table exists
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='changelog'")
    if not c.fetchone():
        print("No changelog table found.")
        conn.close()
        return

    # Get all changelog entries for this entity
    if len(entity_id) == 36:
        sql = "SELECT raw_json FROM changelog WHERE entity_id = ? ORDER BY rowid"
        params = [entity_id]
    else:
        sql = "SELECT raw_json FROM changelog WHERE entity_id LIKE ? ORDER BY rowid"
        params = [f"{entity_id}%"]

    c.execute(sql, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        print(f"No changelog entries for entity {entity_id}.")
        return

    # Filter to completion-relevant events
    completions = []
    for (raw,) in rows:
        entry = json.loads(raw)
        changes = entry.get("changes", [])

        is_completion = any(
            ch.get("attribute") == "resolution" and ch.get("new") == "completed"
            for ch in changes
        )
        is_reset = entry["operation"] == "cascade" and any(
            ch.get("attribute") == "resolution" and ch.get("new") == "unresolved"
            for ch in changes
        )

        if is_completion or is_reset:
            completions.append(entry)

    if not completions:
        print(f"No completion events for entity {entity_id}.")
        return

    # Also get the initial next_due from all changelog entries (set at creation or earlier reset)
    active_next_due = None
    for (raw,) in rows:
        entry = json.loads(raw)
        changes = entry.get("changes", [])
        for ch in changes:
            if ch.get("attribute") == "next_due":
                val = ch.get("value") or ch.get("new")
                if val:
                    active_next_due = val
                    break
        if active_next_due:
            break

    ename = completions[0].get("entity_name", "?")
    print(f"Completion history for \"{ename}\" ({len(completions)} events):")
    print(f"{'Timestamp':<22} {'Event':<12} {'On Time':<9} {'Details'}")
    print("-" * 90)

    prev_ts = None
    for entry in completions:
        ts = entry["timestamp"][:19]  # trim microseconds
        changes = entry.get("changes", [])

        is_completion = any(
            ch.get("attribute") == "resolution" and ch.get("new") == "completed"
            for ch in changes
        )

        on_time_label = ""
        if is_completion:
            event_label = "completed"
            # Compare completion timestamp against active next_due
            if active_next_due:
                try:
                    from datetime import datetime as _dt
                    completion_date = _dt.fromisoformat(ts).date()
                    due_date = date.fromisoformat(active_next_due)
                    if completion_date <= due_date:
                        on_time_label = "yes"
                    else:
                        days_late = (completion_date - due_date).days
                        on_time_label = f"late ({days_late}d)"
                except (ValueError, TypeError):
                    on_time_label = "?"
            details_parts = []
            for ch in changes:
                if ch.get("attribute") == "resolution":
                    continue
                if "old" in ch:
                    details_parts.append(f"{ch['attribute']}: {ch['old']}→{ch['new']}")
                else:
                    details_parts.append(f"{ch['attribute']}: {ch['value']}")
            details = ", ".join(details_parts) if details_parts else ""
        else:
            event_label = "Reset"
            # Extract next_due from changes and update active_next_due
            next_due = None
            streak_val = None
            for ch in changes:
                if ch.get("attribute") == "next_due":
                    next_due = ch.get("value") or ch.get("new")
                if ch.get("attribute") == "streak":
                    streak_val = ch.get("value") or ch.get("new")
            if next_due:
                active_next_due = next_due  # Track for next completion comparison
            details_parts = []
            if next_due:
                details_parts.append(f"next_due={next_due}")
            if streak_val:
                details_parts.append(f"streak={streak_val}")
            details = ", ".join(details_parts)

        # Days since last event
        gap = ""
        if prev_ts:
            try:
                from datetime import datetime as _dt2
                prev_dt = _dt2.fromisoformat(prev_ts)
                curr_dt = _dt2.fromisoformat(ts)
                delta = (curr_dt - prev_dt).days
                if delta > 0:
                    gap = f" (+{delta}d)"
            except (ValueError, TypeError):
                pass

        print(f"  {ts}{gap:<8} {event_label:<12} {on_time_label:<9} {details}")
        prev_ts = ts


def cmd_theme(args):
    """List themes or filter work entities by theme."""
    conn = get_conn()
    c = conn.cursor()

    if not args:
        # List all themes with entity counts
        c.execute("""
            SELECT theme, COUNT(*) as count
            FROM entities
            WHERE theme IS NOT NULL AND theme != '' AND meta_status = 'live'
            GROUP BY theme
            ORDER BY count DESC, theme ASC
        """)
        rows = c.fetchall()
        conn.close()
        if not rows:
            print("No themed entities found.")
            return
        print("Themes:")
        for theme, count in rows:
            print(f"  {theme:<30} {count} entit{'y' if count == 1 else 'ies'}")
    else:
        # Filter entities by theme
        theme_val = args[0]
        c.execute("""
            SELECT id, name, type, focus, life_stage, resolution, importance_tactical
            FROM entities
            WHERE theme = ? AND meta_status = 'live'
            ORDER BY type, name
        """, (theme_val,))
        rows = c.fetchall()
        conn.close()
        if not rows:
            print(f"No entities with theme '{theme_val}'.")
            return
        print(f"Theme: {theme_val} ({len(rows)} entit{'y' if len(rows) == 1 else 'ies'})")
        for eid, name, etype, focus, life_stage, resolution, imp_tac in rows:
            row_dict = {"focus": focus, "life_stage": life_stage, "resolution": resolution,
                        "importance_tactical": imp_tac}
            dims = format_dims(row_dict)
            print(f"  [{etype:<8}] {dims:<35} {name} [{eid[:8]}]")


COMMANDS = {
    "pending": cmd_pending,
    "active": cmd_active,
    "entity": cmd_entity,
    "find": cmd_find,
    "type": cmd_type,
    "relationships": cmd_relationships,
    "stats": cmd_stats,
    "children": cmd_children,
    "tree": cmd_tree,
    "dim": cmd_dim,
    "workable": cmd_workable,
    "stuck": cmd_stuck,
    "by": cmd_by,
    "unprocessed": cmd_unprocessed,
    "search": cmd_search,
    "changelog": cmd_changelog,
    "due": cmd_due,
    "chores": cmd_chores,
    "triggers": cmd_triggers,
    "trigger-history": cmd_trigger_history,
    "completion-history": cmd_completion_history,
    "theme": cmd_theme,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd not in COMMANDS:
        print(f"Unknown command: {cmd}")
        print(f"Available: {', '.join(COMMANDS.keys())}")
        sys.exit(1)

    COMMANDS[cmd](sys.argv[2:])


if __name__ == "__main__":
    main()
