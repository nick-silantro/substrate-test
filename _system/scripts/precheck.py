#!/usr/bin/env python3
"""
Substrate operation pre-checker.

Validates entity operations against the schema before execution.
Can be used standalone or imported by create-entity.py / update-entity.py.

Standalone usage:
  python3 precheck.py create --type task --name "Do the thing" --belongs_to UUID
  python3 precheck.py update UUID --focus active --life-stage in_progress
  python3 precheck.py update UUID --life-stage ready --caller human

Module usage:
  from precheck import validate_create, validate_update
  result = validate_create(schema, "task", dimensions={"focus": "Banana"}, db_path=DB_PATH)
  if not result.valid:
      for e in result.errors:
          print(f"  ERROR: {e}")

What it catches:
  - Unknown types
  - Invalid dimension values (e.g., --focus Banana)
  - Forbidden attributes for a type (e.g., --attr endstate=X on a task)
  - Invalid enum values on type-specific attributes
  - Missing required attributes (creation only, as warnings)
  - Unknown relationship names
  - Target entities that don't exist
  - Connection rule violations (type restrictions on relationships)
  - Same-type nesting violations (task can't belong-to task)
  - Missing required relationships (creation only, as warnings)
  - Missing brief.md for project-grouping entities on life_stage updates (advisory warnings)

Exit codes (standalone):
  0 = valid (may have warnings)
  1 = invalid (has errors)
"""

import os
import sys
import glob as _glob
import sqlite3
import argparse
from datetime import datetime
from schema import load_schema
from lib.fileio import safe_write


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

class ValidationResult:
    """Collects errors (fatal) and warnings (advisory) from validation checks."""

    def __init__(self):
        self.errors = []
        self.warnings = []

    @property
    def valid(self):
        return len(self.errors) == 0

    def error(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def merge(self, other):
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)

    def print_report(self, summary=""):
        """Print a human-readable validation report."""
        if summary:
            print(f"Validating: {summary}\n")
        if self.errors:
            print("ERRORS:")
            for e in self.errors:
                print(f"  ✗ {e}")
        if self.warnings:
            if self.errors:
                print()
            print("WARNINGS:")
            for w in self.warnings:
                print(f"  ⚠ {w}")
        if not self.errors and not self.warnings:
            print("No issues found.")
        status = "VALID" if self.valid else "INVALID"
        print(f"\nResult: {status} ({len(self.errors)} error{'s' if len(self.errors) != 1 else ''}, "
              f"{len(self.warnings)} warning{'s' if len(self.warnings) != 1 else ''})")


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_type(schema, entity_type):
    """Verify the entity type exists in the schema."""
    result = ValidationResult()
    if entity_type not in schema.known_types:
        result.error(
            f"Unknown type '{entity_type}'. "
            f"Known types: {', '.join(sorted(schema.known_types))}"
        )
    return result


def check_dimensions(schema, entity_type, dimensions):
    """Verify dimension names and values are valid for this type.

    Args:
        dimensions: dict of {dimension_name: value} — only user-provided values,
                    not auto-populated defaults.
    """
    result = ValidationResult()
    if entity_type not in schema.known_types:
        return result

    config = schema.dimension_config(entity_type)

    for dim, val in dimensions.items():
        if val is None:
            continue

        # Unknown dimension name
        if dim not in schema.dimension_names:
            result.error(
                f"Unknown dimension '{dim}'. "
                f"Valid dimensions: {', '.join(schema.dimension_names)}"
            )
            continue

        # Disallowed for this type
        if config.get(dim) == "disallowed":
            result.warn(
                f"Dimension '{dim}' is disallowed for type '{entity_type}' — will be ignored"
            )
            continue

        # Invalid value
        valid_values = schema.dimension_values(dim)
        if valid_values and val not in valid_values:
            result.error(
                f"Invalid value '{val}' for dimension '{dim}'. "
                f"Valid values: {', '.join(valid_values)}"
            )

    return result


def check_attrs(schema, entity_type, extra_attrs, is_create=False):
    """Verify extra attributes are valid for this type.

    Uses the attribute-centric access model: access_level() determines whether an
    attribute is forbidden (error), known (validated), or unknown (warning).

    Args:
        extra_attrs: list of (key, value) tuples from --attr flags.
        is_create: if True, also checks that required attributes are present.
    """
    result = ValidationResult()
    if entity_type not in schema.known_types:
        return result

    type_attrs = schema.type_attrs(entity_type)
    provided = {k for k, _ in extra_attrs}

    for attr_name, attr_value in extra_attrs:
        # Resolve access level for this attribute on this type
        access = schema.access_level(attr_name, entity_type, "attribute")

        if access == "forbidden":
            result.error(f"Attribute '{attr_name}' is not available for type '{entity_type}'")
            continue

        if access is None:
            # Dimension passed via --attr: validate its value.
            # Routing to dim_updates happens in update-entity.py after precheck runs.
            if attr_name in schema.dimension_names:
                valid_values = schema.dimension_values(attr_name)
                if valid_values and attr_value not in valid_values:
                    result.error(
                        f"Invalid value '{attr_value}' for dimension '{attr_name}'. "
                        f"Valid values: {', '.join(valid_values)}"
                    )
                continue
            # Genuinely unknown — not an attribute or a dimension
            result.warn(f"Unknown attribute '{attr_name}' (not defined in schema)")
            continue

        # Attribute is accessible (required, preferred, or optional)
        # Check enum values if this attribute has a definition we can look up
        enum_vals = schema.enum_values(entity_type, attr_name)
        if enum_vals and attr_value not in enum_vals:
            result.error(
                f"Invalid value '{attr_value}' for attribute '{attr_name}'. "
                f"Valid values: {', '.join(enum_vals)}"
            )

    # Required attributes (create only) — check required+preferred attributes
    if is_create:
        for attr_name, attr_def in type_attrs.items():
            if not attr_def.get("required"):
                continue
            if attr_name not in provided:
                result.warn(
                    f"Required attribute '{attr_name}' not provided for type '{entity_type}'"
                )

    return result


def _lookup_entity(db_path, entity_id):
    """Look up entity name and type from SQLite. Returns dict or None."""
    if not db_path or not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "SELECT name, type FROM entities WHERE id = ? OR id LIKE ?",
        (entity_id, f"{entity_id}%"),
    )
    row = c.fetchone()
    conn.close()
    return {"name": row[0], "type": row[1]} if row else None


def _check_connection_rule(schema, source_type, rel_name, target_type, target_name):
    """Check a single relationship against connection_rules.

    Returns an error message string if the connection violates a rule, or None.

    Rule types:
      - Universal (no context or source_natures key): restricted to specific
        source→target type pairs globally. E.g., child_of is person→person only.
      - Nature-scoped (has source_natures key): applies when the source type's
        nature set intersects with source_natures. E.g., belongs_to for object-nature
        entities. Evaluated after universal rules.
      - Context-scoped (has context key, no source_natures): restriction applies
        only when source_type matches the context string (compared against type name).
        E.g., belongs_to from a task must target a ticket.

    Evaluation order: universal → nature-scoped → context-scoped.
    The first category that has at least one matching rule applies; later categories
    are only reached if no earlier category matched.
    """
    restricted = schema.relationships.get("connection_rules", {}).get("restricted", [])
    rel_rules = [r for r in restricted if r["relationship"] == rel_name]

    if not rel_rules:
        return None  # No restrictions for this relationship

    universal_rules = [r for r in rel_rules if not r.get("context") and not r.get("source_natures")]
    nature_rules = [r for r in rel_rules if r.get("source_natures")]
    context_rules = [r for r in rel_rules if r.get("context") and not r.get("source_natures")]

    # Universal rules: source type must be in aggregated source_types
    if universal_rules:
        all_sources = set()
        for r in universal_rules:
            all_sources.update(r.get("source_types", []))

        if source_type not in all_sources:
            # Source not covered by any universal rule — fall through to nature/context
            pass
        else:
            # Source is allowed — check target against the matching rule
            for r in universal_rules:
                if source_type in r.get("source_types", []):
                    allowed = r.get("target_types", [])
                    if target_type not in allowed:
                        return (
                            f"{source_type} can only '{rel_name}' "
                            f"{', '.join(allowed)}, not {target_type} ('{target_name}')"
                        )
                    return None

    # Nature-scoped rules: fire when source_type's nature intersects source_natures
    if nature_rules:
        source_natures = set(schema.nature(source_type) or [])
        matching_nature = [
            r for r in nature_rules
            if source_natures & set(r.get("source_natures", []))
        ]
        if matching_nature:
            target_natures = set(schema.nature(target_type) or [])
            for r in matching_nature:
                allowed_natures = set(r.get("target_natures", []))
                allowed_types = r.get("target_types", [])
                # Rule passes if target matches by nature OR by explicit type list
                nature_match = bool(allowed_natures and target_natures & allowed_natures)
                type_match = bool(allowed_types and target_type in allowed_types)
                if not nature_match and not type_match:
                    # Build a readable allowed description
                    desc_parts = []
                    if allowed_natures:
                        desc_parts.append(f"nature {', '.join(sorted(allowed_natures))}")
                    if allowed_types:
                        desc_parts.append(', '.join(sorted(allowed_types)))
                    return (
                        f"{source_type} can only '{rel_name}' entities of "
                        f"{' or '.join(desc_parts)}, not {target_type} ('{target_name}')"
                    )
            return None

    # Context-specific rules: only fire when source_type matches context
    matching = [r for r in context_rules if r.get("context") == source_type]
    if matching:
        for r in matching:
            allowed = r.get("target_types", [])
            if target_type not in allowed:
                return (
                    f"{source_type} can only '{rel_name}' "
                    f"{', '.join(allowed)}, not {target_type} ('{target_name}')"
                )
        return None

    # No applicable rule — permitted by default
    return None


def check_relationships(schema, entity_type, relationships, db_path=None):
    """Verify relationships: names exist, targets exist, connection rules pass.

    Args:
        relationships: list of (relationship_name, target_id) tuples.
        db_path: path to SQLite DB for entity existence checks (optional).
    """
    result = ValidationResult()

    for rel_name, target_id in relationships:
        # Valid relationship name
        if rel_name not in schema.relationship_names:
            result.error(f"Unknown relationship '{rel_name}'")
            continue

        # Target exists
        target_info = _lookup_entity(db_path, target_id)
        if db_path and not target_info:
            result.error(f"Target entity '{target_id}' not found")
            continue

        # Can't check connection rules without knowing both types
        if not target_info or entity_type not in schema.known_types:
            continue

        target_type = target_info["type"]

        # No same-type nesting (scoped to specific relationship categories)
        no_nest = schema.relationships.get("connection_rules", {}).get("no_same_type_nesting", {})
        if (no_nest.get("enabled")
                and entity_type == target_type
                and entity_type in no_nest.get("applies_to", [])):
            # Check scope: only apply to relationships in the scoped category
            scope_cat = no_nest.get("scope_category")
            rel_in_scope = True
            if scope_cat:
                cat_rels = schema.relationships.get("categories", {}).get(scope_cat, {}).get("relationships", {})
                # Check both forward and inverse names
                rel_in_scope = rel_name in cat_rels or any(
                    r.get("inverse") == rel_name for r in cat_rels.values()
                )
            if rel_in_scope:
                result.error(
                    f"Same-type nesting not allowed: "
                    f"{entity_type} cannot '{rel_name}' another {entity_type}"
                )

        # Connection rules
        err = _check_connection_rule(
            schema, entity_type, rel_name, target_type, target_info["name"]
        )
        if err:
            result.error(err)

    return result


def check_recurrence_presence(schema, entity_type, extra_attrs):
    """Check that entities with required recurrence block declare a schedule_type.

    Derives requirement from block access model rather than hardcoding nature check.
    Required = error (missing schedule_type is a validation failure).
    Preferred = warning (encouraged but not enforced).
    schedule_type: none is valid — it means "does not recur."
    Content validation (inter-attribute dependencies) is handled by create-entity.py.

    Args:
        extra_attrs: list of (key, value) tuples from --attr flags.
    """
    result = ValidationResult()
    if entity_type not in schema.known_types:
        return result

    recurrence_access = schema.access_level("recurrence", entity_type, "attribute")

    if recurrence_access not in ("required", "preferred"):
        return result

    has_schedule_type = any(
        k == "recurrence.schedule_type" for k, _ in extra_attrs
    )
    if not has_schedule_type:
        if recurrence_access == "required":
            result.error(
                f"Type '{entity_type}' must declare recurrence schedule "
                f"(use --attr recurrence.schedule_type=none if non-recurring)"
            )
        else:
            result.warn(
                f"Type '{entity_type}' should declare recurrence schedule "
                f"(use --attr recurrence.schedule_type=none if non-recurring)"
            )

    return result


def _has_doc_matching(entity_folder, pattern):
    """Return True if entity_folder contains any file whose name contains pattern."""
    return bool(_glob.glob(os.path.join(entity_folder, f"*{pattern}*")))


# ---------------------------------------------------------------------------
# Entity-based review queries (messaging grouping)
# These query the SQLite index for review entities instead of scanning files.
# Both paths (file-based and entity-based) are checked — either can satisfy a gate.
# ---------------------------------------------------------------------------

def _query_review_entities(db_path, parent_entity_id, gate=None, performer_type=None,
                           reviewer_role=None):
    """Query for review entities belonging to a parent entity.

    Args:
        db_path: Path to substrate.db
        parent_entity_id: The ticket/project entity ID to search under
        gate: Optional filter — 'pre_execution' or 'post_execution'
        performer_type: Optional filter — 'agent' or 'user' (checks performed_by target type)
        reviewer_role: Optional filter — 'peer', 'owner', or 'user' (reads reviewer_role attr)

    Returns:
        List of dicts with keys: id, verdict, performer_type, gate, reviewer_role, phase
    """
    if not db_path:
        return []

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # Find full entity ID (may be short form)
    c.execute("SELECT id FROM entities WHERE id = ? OR id LIKE ?",
              (parent_entity_id, f"{parent_entity_id}%"))
    id_row = c.fetchone()
    if not id_row:
        conn.close()
        return []
    full_parent_id = id_row[0]

    # Find review entities that belong_to this parent
    c.execute("""
        SELECT e.id, e.path
        FROM entities e
        JOIN relationships r ON r.source_id = e.id
        WHERE e.type = 'review'
        AND e.meta_status = 'live'
        AND r.relationship = 'belongs_to'
        AND r.target_id = ?
    """, (full_parent_id,))
    rows = c.fetchall()

    results = []
    for row in rows:
        review_id, review_path = row

        # Read gate and verdict from meta.yaml
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        meta_path = os.path.join(substrate_path, review_path, "meta.yaml")
        review_gate = None
        review_verdict = None
        review_reviewer_role = None
        review_phase = None
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("gate:"):
                        review_gate = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("verdict:"):
                        review_verdict = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("reviewer_role:"):
                        review_reviewer_role = stripped.split(":", 1)[1].strip().strip('"').strip("'")
                    elif stripped.startswith("phase:"):
                        review_phase = stripped.split(":", 1)[1].strip().strip('"').strip("'")

        # Filter by gate if specified
        if gate and review_gate != gate:
            continue

        # Filter by reviewer_role if specified
        if reviewer_role and review_reviewer_role != reviewer_role:
            continue

        # Resolve performer type from performed_by relationship
        perf_type = None
        c.execute("""
            SELECT e.type FROM relationships r
            JOIN entities e ON r.target_id = e.id
            WHERE r.source_id = ? AND r.relationship = 'performed_by'
            LIMIT 1
        """, (review_id,))
        perf_row = c.fetchone()
        if perf_row:
            perf_type = perf_row[0]

        # Filter by performer type if specified
        if performer_type and perf_type != performer_type:
            continue

        results.append({
            "id": review_id,
            "verdict": review_verdict,
            "performer_type": perf_type,
            "gate": review_gate,
            "reviewer_role": review_reviewer_role,
            "phase": review_phase,
        })

    conn.close()
    return results


def _has_entity_review(db_path, parent_entity_id, gate=None, performer_type=None,
                       reviewer_role=None, require_pass=False, require_established=False):
    """Check if a review entity exists and optionally has a passing verdict.

    Args:
        db_path: Path to substrate.db
        parent_entity_id: The ticket/project entity ID
        gate: 'pre_execution' or 'post_execution'
        performer_type: 'agent' or 'user' (legacy — prefer reviewer_role)
        reviewer_role: 'peer', 'owner', or 'user'
        require_pass: If True, verdict must be 'pass' (case-insensitive)
        require_established: If True, only phase=established reviews count (retired excluded)

    Returns:
        True  — matching review exists (and passes/is established if those flags are set)
        False — matching review exists but fails verdict or established check
        None  — no matching review found
    """
    reviews = _query_review_entities(db_path, parent_entity_id, gate=gate,
                                     performer_type=performer_type, reviewer_role=reviewer_role)
    if not reviews:
        return None

    for review in reviews:
        verdict = review.get("verdict")
        phase = review.get("phase")

        # require_established: only phase=established reviews count (forming and retired both excluded)
        if require_established and phase != "established":
            continue

        if not verdict or verdict == "pending":
            continue  # No verdict yet
        if require_pass:
            if verdict.lower().startswith("pass"):
                return True
        else:
            # Any non-pending verdict counts as "has verdict"
            return True

    # Reviews exist but none satisfy all requirements
    return False


def _has_active_nonpass_reviews(db_path, parent_entity_id, gate=None, reviewer_role=None):
    """Check if any established non-pass reviews exist on a ticket.

    A gate should not clear if there are active (established) conditional or fail
    verdicts — even if a passing review also exists. The non-pass reviews represent
    unresolved concerns that must be addressed (retired) before advancement.

    Args:
        db_path: Path to substrate.db
        parent_entity_id: The ticket/project entity ID
        gate: 'pre_execution' or 'post_execution' — scope the check to one gate
        reviewer_role: Optional filter by reviewer_role

    Returns:
        True  — at least one established non-pass review exists (gate should block)
        False — no established non-pass reviews found (gate is clear from this perspective)
    """
    reviews = _query_review_entities(db_path, parent_entity_id, gate=gate,
                                     reviewer_role=reviewer_role)
    if not reviews:
        return False

    for review in reviews:
        phase = review.get("phase")
        verdict = review.get("verdict")

        if phase != "established":
            continue
        if not verdict or verdict == "pending":
            continue
        if not verdict.lower().startswith("pass"):
            return True  # Found an established non-pass — gate should block

    return False


def _all_reviews_retired(db_path, parent_entity_id):
    """Check whether all review entities attached to a ticket are retired.

    Used by the under_review re-entry gate: after a fail cascade, the L2 must
    explicitly retire all outstanding conditional/fail reviews before resubmitting.

    Returns:
        True  — no review entities exist, or all are phase=retired
        False — at least one review entity is not retired (phase=established or forming)
    """
    reviews = _query_review_entities(db_path, parent_entity_id)
    if not reviews:
        return True  # No reviews — first submission, trivially passes

    for review in reviews:
        phase = review.get("phase")
        if phase != "retired":
            return False

    return True


def _has_doc_review_approval(entity_folder):
    """Check whether a review doc exists and contains a completed verdict.

    Review docs are identified by files ending in '-review.md' or '_review.md'
    (e.g., 'engagement-review.md'). Files that merely contain 'review' mid-name
    (e.g., 'doc-review-plan.md') are excluded.

    Verdict detection: looks for a line containing 'Verdict:' (with or without
    bold markdown markers) followed by non-empty, non-placeholder content on the
    same line or the next line. The bold form (**Verdict:**) is the documented
    convention; the bare form (Verdict:) is accepted as a fallback to avoid
    silent gate blocks when reviewers omit the markers.

    Returns:
        True  — review doc exists and has a non-empty, non-placeholder verdict
        False — review doc exists but no verdict found (review not completed)
        None  — no review doc found at all
    """
    # Match files ending in '-review.md' or '_review.md'
    review_files = (
        _glob.glob(os.path.join(entity_folder, "*-review.md"))
        + _glob.glob(os.path.join(entity_folder, "*_review.md"))
    )
    if not review_files:
        return None

    def _extract_verdict_content(line, next_line=None):
        """Return non-empty, non-placeholder content after 'Verdict:' or None."""
        # Try bold form first, then bare form
        for marker in ("**Verdict:", "Verdict:"):
            if marker in line:
                after = line.split(marker, 1)[1].strip().lstrip("*").strip()
                if after and not after.startswith("_"):
                    return after
                # Check next line
                if next_line is not None:
                    stripped = next_line.strip()
                    if stripped and not stripped.startswith("_"):
                        return stripped
        return None

    for rf in review_files:
        try:
            with open(rf, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "Verdict:" in line:
                next_line = lines[i + 1] if i + 1 < len(lines) else None
                if _extract_verdict_content(line, next_line) is not None:
                    return True
    return False


def _has_user_review_section(entity_folder):
    """Check whether a review doc contains a User Review section with content.

    Looks for a heading containing 'User Review' (e.g., '## User Review',
    '### User Review Findings') and checks that at least one non-empty line
    follows before the next heading or end of file.

    Returns:
        True  — review doc exists and has a User Review section with content
        False — review doc exists but no User Review section or section is empty
        None  — no review doc found
    """
    review_files = (
        _glob.glob(os.path.join(entity_folder, "*-review.md"))
        + _glob.glob(os.path.join(entity_folder, "*_review.md"))
    )
    if not review_files:
        return None

    for review_file in review_files:
        with open(review_file, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.splitlines()
        in_section = False
        for line in lines:
            if in_section:
                stripped = line.strip()
                if stripped.startswith("#"):
                    # Hit next heading without finding content
                    in_section = False
                    continue
                if stripped:
                    return True
            if line.strip().startswith("#") and "User Review" in line:
                in_section = True
    return False


def _has_user_check_approval(entity_folder):
    """Check whether a user check doc exists and contains a Pass verdict.

    User check docs are identified by files ending in '-user-check.md' or
    '_user-check.md'. Unlike BSC docs, user check files support multiple
    appended checks — the last Verdict is the current state.

    Returns:
        True  — user check doc exists and last verdict begins with "Pass"
        False — user check doc exists but no verdict, or last verdict is not "Pass"
        None  — no user check doc found at all
    """
    check_files = (
        _glob.glob(os.path.join(entity_folder, "*-user-check.md"))
        + _glob.glob(os.path.join(entity_folder, "*_user-check.md"))
    )
    if not check_files:
        return None

    for cf in check_files:
        try:
            with open(cf, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        lines = content.splitlines()
        last_verdict = None
        for i, line in enumerate(lines):
            if "Verdict:" in line:
                next_line = lines[i + 1] if i + 1 < len(lines) else None
                verdict_content = _extract_verdict_content(line, next_line)
                if verdict_content is not None:
                    last_verdict = verdict_content
        if last_verdict is not None:
            if last_verdict.lower().startswith("pass"):
                return True
            return False
    return False


def _has_bsc_approval(entity_folder):
    """Check whether a BSC doc exists and contains a completed verdict.

    BSC docs are identified by files ending in '-bsc.md' or '_bsc.md'
    (e.g., 'recurrence-bsc.md'). Files that merely contain 'bsc' mid-name
    (e.g., 'my-bsc-notes.md') are excluded.

    Verdict detection: looks for a line containing 'Verdict:' (with or without
    bold markdown markers) followed by content that begins with "Pass" (case-
    insensitive). Any other verdict value — "Conditional", "Fix required", "Fail"
    — does NOT approve the gate. This is intentional: a BSC with required fixes
    must not allow the ticket to advance to ready.

    Returns:
        True  — BSC doc exists and verdict begins with "Pass"
        False — BSC doc exists but no verdict, or verdict is not "Pass"
        None  — no BSC doc found at all
    """
    bsc_files = (
        _glob.glob(os.path.join(entity_folder, "*-bsc.md"))
        + _glob.glob(os.path.join(entity_folder, "*_bsc.md"))
    )
    if not bsc_files:
        return None

    def _extract_verdict_content(line, next_line=None):
        """Return non-empty, non-placeholder content after 'Verdict:' or None."""
        for marker in ("**Verdict:", "Verdict:"):
            if marker in line:
                after = line.split(marker, 1)[1].strip().lstrip("*").strip()
                if after and not after.startswith("_"):
                    return after
                if next_line is not None:
                    stripped = next_line.strip()
                    if stripped and not stripped.startswith("_"):
                        return stripped
        return None

    for bf in bsc_files:
        try:
            with open(bf, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError:
            continue
        lines = content.splitlines()
        for i, line in enumerate(lines):
            if "Verdict:" in line:
                next_line = lines[i + 1] if i + 1 < len(lines) else None
                verdict_content = _extract_verdict_content(line, next_line)
                if verdict_content is not None:
                    # Only "Pass" (case-insensitive) approves the BSC gate.
                    # "Conditional", "Fix required", "Fail", etc. are not approvals.
                    if verdict_content.lower().startswith("pass"):
                        return True
                    return False  # Verdict found but not a pass
    return False


# Document presence requirements by engagement mode.
# Keys are glob fragments checked against filenames in the entity folder.
# execute: doctrine + plan + trace; BSC approval always required (checked separately)
# lean: no pre-work docs; no BSC
# experiment: hypothesis + trace
# explore: trace
# wander / none: no pre-execution doc requirements
#
# Note: all active engagement modes require a review doc at under_review.
# This is enforced via check_under_review_gate and check_done_working_gate.
# REVIEW_EXEMPT_MODES lists the only modes that skip the review gate entirely.
# Wander requires a review doc (the "yes, and" response) plus a harvest gate
# (outbound relationship to at least one produced entity).
REVIEW_EXEMPT_MODES = {"none"}


MODE_DOC_REQUIREMENTS = {
    "execute": ["doctrine", "plan", "trace"],
    "lean": [],
    "experiment": ["hypothesis", "trace"],
    "explore": ["trace"],
    "wander": [],
    "none": [],
}


def check_ready_gate(schema, entity_id, entity_type, life_stage_value,
                     db_path=None, caller="agent", substrate_path=None):
    """Check ready-gate requirements when life_stage transitions to 'ready'.

    Engagement modes require specific documents in the entity folder before
    an entity can move to 'ready'. Document presence is checked by filename
    pattern (e.g., any file containing 'doctrine' satisfies the doctrine requirement).

    caller: 'agent' (hard gate — missing docs become errors, blocks operation)
            'human' (soft gate — missing docs become warnings, _ready-gate-override.md
                     is written to the entity folder recording what was missing and when)

    Returns a ValidationResult.
    """
    result = ValidationResult()

    # Gate fires at ready and in_progress only; under_review and done_working
    # are handled by check_under_review_gate and check_done_working_gate.
    if life_stage_value not in ("ready", "in_progress"):
        return result

    if not db_path or not entity_id:
        return result

    # Look up entity's engagement_mode and path from SQLite
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT engagement_mode, path, name FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()

    if not row:
        return result

    mode = row[0]
    entity_path = row[1]
    entity_name = row[2] or entity_id

    # Human-readable label for the transition being attempted
    action_labels = {
        "ready": "moving to ready",
        "in_progress": "starting work",
    }
    action = action_labels.get(life_stage_value, f"transitioning to {life_stage_value}")

    # Skip engagement_mode check for types where the field is forbidden
    em_access = schema.access_level("engagement_mode", entity_type, "attribute")
    if em_access == "forbidden":
        return result  # engagement_mode is not applicable to this type

    # Block when engagement_mode is none (unassigned) — at all WIP transitions
    if not mode or mode == "none":
        msg = (
            f"engagement_mode is '{mode or 'unset'}' — assign an engagement mode "
            f"before {action} (execute, lean, experiment, explore, or wander)"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
        return result

    # Resolve entity folder
    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
    entity_folder = os.path.join(substrate_path, entity_path)

    # User check gate: tickets only. Human counterpart to the agent BSC.
    if entity_type == "ticket":
        meta_path = os.path.join(entity_folder, "meta.yaml")
        user_check_required = False  # default if absent
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("user_check_required:"):
                        val = line.strip().split(":", 1)[1].strip().lower()
                        user_check_required = val in ("true", "yes")
                        break

        if user_check_required:
            # Check both paths: file-based user check OR entity-based review
            uc_approval = _has_user_check_approval(entity_folder)
            if uc_approval is None:
                # Try entity-based: review entity with gate=pre_execution, reviewer_role=user
                uc_approval = _has_entity_review(
                    db_path, entity_id, gate="pre_execution",
                    reviewer_role="user", require_pass=True, require_established=True)
            if uc_approval is None:
                # Legacy fallback: performed_by user (pre-reviewer_role entities)
                uc_approval = _has_entity_review(
                    db_path, entity_id, gate="pre_execution",
                    performer_type="user", require_pass=True)
            if uc_approval is None:
                uc_msg = (
                    f"WIP gate: user_check_required is true but no user check found "
                    f"(file or review entity) — human blind spot check needed before {action}"
                )
            elif uc_approval is False:
                uc_msg = (
                    f"WIP gate: user check found but verdict is not 'Pass' "
                    f"— user check must pass before {action}"
                )
            else:
                uc_msg = None

            if uc_approval is not True:
                if caller == "agent":
                    result.error(uc_msg)
                else:
                    result.warn(uc_msg)
                return result

    # BSC approval gate: execute mode always requires a BSC doc with completed verdict.
    # Gate clears ONLY when: at least one established pass exists AND no established
    # non-pass (conditional/fail) reviews remain. Active non-pass reviews represent
    # unresolved concerns that block advancement regardless of passing reviews.
    if mode == "execute":
        # Check both paths: file-based BSC OR entity-based review
        approval = _has_bsc_approval(entity_folder)
        if approval is None:
            # Try entity-based: review entity with gate=pre_execution, reviewer_role=peer
            approval = _has_entity_review(
                db_path, entity_id, gate="pre_execution",
                reviewer_role="peer", require_pass=True, require_established=True)
        if approval is None:
            # Legacy fallback: performed_by agent (pre-reviewer_role entities)
            approval = _has_entity_review(
                db_path, entity_id, gate="pre_execution",
                performer_type="agent", require_pass=True)
        if approval is None:
            msg = (
                f"WIP gate: execute mode requires a pre-execution review "
                f"(BSC doc or review entity) with a passing verdict before {action}"
            )
        elif approval is False:
            msg = (
                f"WIP gate: pre-execution review found but verdict is not 'Pass' "
                f"— required fixes must be addressed before {action}"
            )
        elif _has_active_nonpass_reviews(db_path, entity_id, gate="pre_execution"):
            # A pass exists, but so does an unresolved conditional or fail.
            # The non-pass review represents concerns that haven't been addressed
            # (retired). Gate blocks until all non-pass reviews are retired.
            approval = False
            msg = (
                f"WIP gate: a passing pre-execution review exists, but there are also "
                f"active (established) conditional or fail reviews — those concerns must "
                f"be addressed and their reviews retired before {action}"
            )
        else:
            msg = None  # BSC approved — fall through to existence check

        if approval is not True:
            if caller == "agent":
                result.error(msg)
            else:
                result.warn(msg)
            return result

    # Existence gate: checks for mode-required pre-work documents
    if not mode or mode not in MODE_DOC_REQUIREMENTS:
        return result

    required_patterns = MODE_DOC_REQUIREMENTS.get(mode, [])
    if not required_patterns:
        return result

    missing = [p for p in required_patterns if not _has_doc_matching(entity_folder, p)]

    if not missing:
        return result

    msg = (
        f"WIP gate: engagement mode '{mode}' requires documents matching "
        f"{missing} in entity folder before {action}"
    )

    if caller == "agent":
        # Hard gate: block the operation
        result.error(msg)
    else:
        # Soft gate: warn and inject override note
        result.warn(msg)
        _write_ready_gate_override(entity_folder, entity_name, mode, missing)

    return result


def _write_ready_gate_override(entity_folder, entity_name, mode, missing_patterns):
    """Write _ready-gate-override.md to the entity folder recording the override."""
    override_path = os.path.join(entity_folder, "_ready-gate-override.md")
    timestamp = datetime.now().isoformat(timespec="seconds")

    entry = f"""## Override — {timestamp}

**Entity:** {entity_name}
**Engagement mode:** {mode}
**Missing documents:** {", ".join(missing_patterns)}

The ready-gate was bypassed by a human caller. The following documents were expected
but not found in this entity folder: {", ".join(f"`*{p}*`" for p in missing_patterns)}

The picking agent should either create the missing document(s) or confirm with the
human that their absence is intentional before proceeding.
"""

    os.makedirs(entity_folder, exist_ok=True)
    with safe_write(override_path, create=True) as (existing, write):
        if not existing:
            content = "# Ready-Gate Override Log\n\n" + entry
        else:
            content = existing + "\n" + entry
        write(content)


def check_under_review_gate(schema, entity_id, entity_type, life_stage_value,
                             db_path=None, caller="agent", substrate_path=None):
    """Check that a review doc exists before under_review transition.

    All active engagement modes require a review doc at under_review. Only
    REVIEW_EXEMPT_MODES (currently just 'none') skip this gate.

    Wander additionally requires a harvest gate: at least one outbound
    relationship to a produced entity (excluding belongs_to and task children).

    Returns a ValidationResult.
    """
    result = ValidationResult()

    if life_stage_value != "under_review":
        return result

    if not db_path or not entity_id:
        return result

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT engagement_mode, path FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()

    if not row:
        return result

    mode = row[0]
    entity_path = row[1]

    # Warn if mode is undeclared — process failure upstream
    if not mode or mode == "none":
        result.warn(
            "ticket reached under_review with engagement_mode=none — "
            "mode was never declared; review may not be meaningful"
        )
        return result

    # Skip exempt modes
    if mode in REVIEW_EXEMPT_MODES:
        return result

    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
    entity_folder = os.path.join(substrate_path, entity_path)

    # Re-entry gate: if any review entities exist, all must be retired before
    # re-entering under_review. This enforces that the L2 has explicitly acknowledged
    # all outstanding conditionals/fails from a prior review cycle before resubmitting.
    # First-time entry (no review entities) passes trivially.
    if db_path and not _all_reviews_retired(db_path, entity_id):
        msg = (
            "WIP gate: outstanding review entities are not yet retired — "
            "address all conditional and fail reviews and retire them before resubmitting. "
            "Passing reviews from the prior cycle are automatically retired on fail cascade."
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
        return result

    # Wander harvest gate — additive: must also have produced at least one entity
    if mode == "wander":
        conn2 = sqlite3.connect(db_path)
        c2 = conn2.cursor()
        # Retrieve full UUID (entity_id may be short form)
        c2.execute("SELECT id FROM entities WHERE id = ? OR id LIKE ?",
                   (entity_id, f"{entity_id}%"))
        id_row = c2.fetchone()
        full_id = id_row[0] if id_row else entity_id
        c2.execute("""
            SELECT COUNT(*) FROM relationships r
            JOIN entities e ON r.target_id = e.id
            WHERE r.source_id = ?
            AND r.relationship != 'belongs_to'
            AND e.type != 'task'
        """, (full_id,))
        count = c2.fetchone()[0]
        conn2.close()
        if count == 0:
            msg = (
                "WIP gate: wander ticket must have at least one outbound relationship "
                "to a produced entity before under_review — harvest the entities your wander produced"
            )
            if caller == "agent":
                result.error(msg)
            else:
                result.warn(msg)

    return result


def check_done_working_gate(schema, entity_id, entity_type, life_stage_value,
                             db_path=None, caller="agent", substrate_path=None):
    """Check that an execution review doc with a completed verdict exists before done_working.

    For execute and lean modes: the execution review doc must exist AND contain a
    completed verdict (a line with '**Verdict:**' followed by non-placeholder content).
    Also handles the skip case: an entity that jumps directly from in_progress to
    done_working still hits this gate.

    Returns a ValidationResult.
    """
    result = ValidationResult()

    if life_stage_value != "done_working":
        return result

    if not db_path or not entity_id:
        return result

    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT engagement_mode, path FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()

    if not row:
        return result

    mode = row[0]
    entity_path = row[1]

    # Preserve null/exempt guard — tasks have no engagement_mode and reach done_working
    if not mode or mode in REVIEW_EXEMPT_MODES:
        return result

    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
    entity_folder = os.path.join(substrate_path, entity_path)

    # Wander carve-out: yes-and response has no verdict by design — existence check only
    if mode == "wander":
        approval = _has_doc_review_approval(entity_folder)
        if approval is None:
            approval = _has_entity_review(db_path, entity_id, gate="post_execution")
        if approval is None:
            msg = (
                "WIP gate: wander ticket must have a review (doc or entity) "
                "before done_working — the yes-and response must be written"
            )
            if caller == "agent":
                result.error(msg)
            else:
                result.warn(msg)
        return result

    # All other active modes: peer review AND owner review required, both passing.
    # AND logic — both must pass. A fail from either blocks even if the other passes.
    #
    # Peer review (reviewer_role=peer): L2 agent focused on the work itself.
    # Owner review (reviewer_role=owner): L1 agent with project context and accountability.
    #
    # Each check: try entity-based (reviewer_role) first, then legacy file-based fallback.

    # --- Peer review check ---
    peer_approval = _has_entity_review(
        db_path, entity_id, gate="post_execution",
        reviewer_role="peer", require_pass=True, require_established=True)
    if peer_approval is None:
        # Legacy fallback: any passing file-based review OR entity without reviewer_role
        file_approval = _has_doc_review_approval(entity_folder)
        if file_approval is None:
            file_approval = _has_entity_review(
                db_path, entity_id, gate="post_execution", require_pass=True)
        peer_approval = file_approval

    if peer_approval is None:
        msg = (
            "WIP gate: no peer review found (reviewer_role=peer, gate=post_execution, "
            "verdict=pass) — an L2 peer reviewer must evaluate this work before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
    elif peer_approval is False:
        msg = (
            "WIP gate: peer review found but verdict is not 'pass' "
            "— peer reviewer must pass this work before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
    elif _has_active_nonpass_reviews(db_path, entity_id, gate="post_execution", reviewer_role="peer"):
        msg = (
            "WIP gate: passing peer review exists, but active (established) conditional "
            "or fail peer reviews also exist — concerns must be addressed and those "
            "reviews retired before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)

    # --- Owner review check ---
    owner_approval = _has_entity_review(
        db_path, entity_id, gate="post_execution",
        reviewer_role="owner", require_pass=True, require_established=True)

    if owner_approval is None:
        msg = (
            "WIP gate: no owner review found (reviewer_role=owner, gate=post_execution, "
            "verdict=pass) — the L1 project owner must review this work before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
    elif owner_approval is False:
        msg = (
            "WIP gate: owner review found but verdict is not 'pass' "
            "— L1 project owner must pass this work before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
    elif _has_active_nonpass_reviews(db_path, entity_id, gate="post_execution", reviewer_role="owner"):
        msg = (
            "WIP gate: passing owner review exists, but active (established) conditional "
            "or fail owner reviews also exist — concerns must be addressed and those "
            "reviews retired before done_working"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)

    # --- User review check (optional) ---
    meta_path = os.path.join(entity_folder, "meta.yaml")
    user_review_required = True  # schema default is true
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("user_review_required:"):
                    val = line.strip().split(":", 1)[1].strip().lower()
                    user_review_required = val not in ("false", "no")
                    break

    if user_review_required:
        user_approval = _has_entity_review(
            db_path, entity_id, gate="post_execution",
            reviewer_role="user", require_pass=True, require_established=True)
        if user_approval is None:
            # Legacy fallback: file-based User Review section or performed_by user entity
            user_review = _has_user_review_section(entity_folder)
            if not user_review:
                entity_user_review = _has_entity_review(
                    db_path, entity_id, gate="post_execution", performer_type="user")
                if entity_user_review:
                    user_review = True
            user_approval = True if user_review else None

        if user_approval is None:
            ur_msg = (
                "WIP gate: user_review_required is true but no user review found "
                "(reviewer_role=user, gate=post_execution, verdict=pass) — "
                "user review must be completed before done_working"
            )
            result.warn(ur_msg)
        elif user_approval is False:
            ur_msg = (
                "WIP gate: user review found but verdict is not 'pass' "
                "— user must approve before done_working"
            )
            result.warn(ur_msg)

    return result


def check_commit_gate(schema, entity_id, entity_type, life_stage_value,
                      db_path=None, caller="agent", substrate_path=None):
    """Check that entity folder files are committed before under_review transition.

    Enforces the git commit protocol: all files in the ticket entity folder must
    be committed to git before the ticket moves to under_review. Implementation
    files outside the entity folder are caught by a softer workspace-level warning.

    Applies to all active engagement modes except REVIEW_EXEMPT_MODES.
    Skips gracefully if git is unavailable or the workspace is not a repo.

    Returns a ValidationResult.
    """
    import subprocess

    result = ValidationResult()

    if life_stage_value != "under_review":
        return result

    if not db_path or not entity_id:
        return result

    # Look up entity's engagement_mode and path from SQLite
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT engagement_mode, path FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()

    if not row:
        return result

    mode = row[0]
    entity_path = row[1]

    # Preserve null/exempt guard — tasks and none-mode entities skip commit check
    if not mode or mode in REVIEW_EXEMPT_MODES:
        return result

    # Resolve paths
    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
    entity_folder = os.path.join(substrate_path, entity_path)

    # Check entity folder for uncommitted files
    try:
        entity_status = subprocess.run(
            ["git", "status", "--porcelain", "--", entity_folder],
            capture_output=True, text=True, cwd=substrate_path, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        result.warn("Commit gate: git not available — cannot verify commit status")
        return result

    if entity_status.returncode != 0:
        result.warn("Commit gate: git status failed — cannot verify commit status")
        return result

    uncommitted = entity_status.stdout.strip()
    if uncommitted:
        file_list = [line.strip() for line in uncommitted.splitlines()]
        msg = (
            f"Commit gate: entity folder has uncommitted files. "
            f"Commit all files before moving to under_review. "
            f"Uncommitted: {', '.join(file_list)}"
        )
        if caller == "agent":
            result.error(msg)
        else:
            result.warn(msg)
        return result

    # Workspace-level check: soft warning for any uncommitted changes anywhere
    try:
        workspace_status = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=substrate_path, timeout=10,
        )
        if workspace_status.returncode == 0 and workspace_status.stdout.strip():
            result.warn(
                "Commit gate: workspace has other uncommitted changes — "
                "verify all ticket-related files are committed"
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # Already warned above if git is unavailable

    return result


def check_brief_presence(schema, entity_type, entity_id, db_path=None, substrate_path=None):
    """Warn if brief.md is absent for project-grouping entities.

    Applies to: project, workstream, initiative, incident (grouping: "efforts").
    Always soft — never blocks an operation. Fires during life_stage updates to
    prompt the operator to add a brief before the entity moves into active work.

    Returns a ValidationResult.
    """
    result = ValidationResult()

    # Only applies to effort-grouping types
    type_def = schema.types.get("types", {}).get(entity_type, {})
    if type_def.get("grouping") != "efforts":
        return result

    if not db_path or not entity_id:
        return result

    # Look up entity path and name
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT path, name FROM entities WHERE id = ? OR id LIKE ?",
              (entity_id, f"{entity_id}%"))
    row = c.fetchone()
    conn.close()

    if not row or not row[0]:
        return result

    entity_path, entity_name = row[0], row[1] or entity_id

    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )

    entity_folder = os.path.join(substrate_path, entity_path)
    brief_path = os.path.join(entity_folder, "brief.md")

    if not os.path.exists(brief_path):
        result.warn(
            f"{entity_type} '{entity_name}' has no brief.md — "
            f"consider adding one (see entity-management skill for the brief.md convention)"
        )

    return result


def check_assessment_resolution_constraint(schema, entity_id, dimensions, db_path=None,
                                            effective_resolution=None):
    """Check that the assessment value is valid for the entity's resolution state.

    Assessment has two value sets:
      - delivery_values (not_assessed, on_track, at_risk, off_track): valid when unresolved
      - outcome_values (not_assessed, exceeded, succeeded, mixed, failed): valid when resolved

    Violations are hard errors — semantically wrong, not just stylistically divergent.

    Args:
        entity_id: entity UUID (used for DB lookup of current resolution on updates)
        dimensions: dict of {dimension_name: value} being set in this operation
        db_path: path to SQLite DB (needed for resolution lookup on updates)
        effective_resolution: if provided, skip DB lookup and use this value directly
                              (used by validate_create where resolution isn't in DB yet)
    """
    result = ValidationResult()
    assessment_value = dimensions.get("assessment")
    if not assessment_value:
        return result  # assessment not being set — nothing to check

    # Determine effective resolution
    if effective_resolution is None:
        # Prefer incoming resolution from this operation
        effective_resolution = dimensions.get("resolution")

    if effective_resolution is None and db_path and entity_id:
        # Fall back to current resolution from SQLite
        entity_info = _lookup_entity(db_path, entity_id)
        if entity_info:
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute(
                "SELECT resolution FROM entities WHERE id = ? OR id LIKE ?",
                (entity_id, f"{entity_id}%"),
            )
            row = c.fetchone()
            conn.close()
            if row:
                effective_resolution = row[0]

    if effective_resolution is None:
        effective_resolution = "unresolved"  # safe default

    valid_values = schema.assessment_values(effective_resolution)
    if assessment_value not in valid_values:
        # Determine the other set for the error message
        if effective_resolution == "unresolved":
            qualifier = "unresolved entities"
            other_qualifier = "resolved entities"
            other_values = schema.assessment_values("completed")
        else:
            qualifier = "resolved entities"
            other_qualifier = "unresolved entities"
            other_values = schema.assessment_values("unresolved")
        result.error(
            f"assessment value '{assessment_value}' is only valid for {other_qualifier}. "
            f"For {qualifier}, valid values: {', '.join(valid_values)}"
        )

    return result


def check_required_relationships(schema, entity_type, relationships):
    """Check that required relationships are provided (creation only)."""
    result = ValidationResult()
    if entity_type not in schema.known_types:
        return result

    required = schema.required_relationships(entity_type)
    provided = {name for name, _ in relationships}

    for req in required:
        rel_name = req["relationship"] if isinstance(req, dict) else req
        if rel_name not in provided:
            notes = req.get("notes", "") if isinstance(req, dict) else ""
            hint = f" ({notes})" if notes else ""
            result.warn(f"Required relationship '{rel_name}' not provided{hint}")

    return result


# ---------------------------------------------------------------------------
# Composite validation functions (used by scripts and CLI)
# ---------------------------------------------------------------------------

def validate_create(schema, entity_type, name=None, description=None,
                    dimensions=None, relationships=None, extra_attrs=None,
                    due=None, db_path=None):
    """Validate a create operation. Returns a ValidationResult."""
    result = ValidationResult()
    result.merge(check_type(schema, entity_type))
    result.merge(check_dimensions(schema, entity_type, dimensions or {}))
    result.merge(check_attrs(schema, entity_type, extra_attrs or [], is_create=True))
    result.merge(check_relationships(schema, entity_type, relationships or [], db_path))
    result.merge(check_required_relationships(schema, entity_type, relationships or []))
    result.merge(check_recurrence_presence(schema, entity_type, extra_attrs or []))
    # Assessment constraint: new entities start unresolved; use incoming resolution if provided
    create_resolution = (dimensions or {}).get("resolution", "unresolved")
    result.merge(check_assessment_resolution_constraint(
        schema, None, dimensions or {}, effective_resolution=create_resolution
    ))
    return result


def validate_update(schema, entity_id, entity_type=None,
                    dimensions=None, relationships=None, extra_attrs=None,
                    db_path=None, caller="agent"):
    """Validate an update operation. Returns a ValidationResult.

    caller: 'agent' (default, hard ready-gate) or 'human' (soft ready-gate + override file)
    """
    result = ValidationResult()

    # Resolve entity type from DB if not provided
    if not entity_type and db_path:
        info = _lookup_entity(db_path, entity_id)
        if info:
            entity_type = info["type"]
        else:
            result.error(f"Entity '{entity_id}' not found")
            return result

    if not entity_type:
        result.warn("Cannot validate without entity type")
        return result

    result.merge(check_dimensions(schema, entity_type, dimensions or {}))
    result.merge(check_attrs(schema, entity_type, extra_attrs or [], is_create=False))
    result.merge(check_relationships(schema, entity_type, relationships or [], db_path))
    # Assessment constraint: validate against current or incoming resolution state
    result.merge(check_assessment_resolution_constraint(
        schema, entity_id, dimensions or {}, db_path=db_path
    ))

    # Ready-gate: check engagement mode document expectations on life_stage → ready
    life_stage_val = (dimensions or {}).get("life_stage")

    # Gate 1 — Ready: pre-work docs + BSC approval (execute mode); fires at ready + in_progress
    if life_stage_val:
        result.merge(check_ready_gate(schema, entity_id, entity_type, life_stage_val,
                                      db_path=db_path, caller=caller))

    # Gate 2 — Under Review: execution review doc must exist
    if life_stage_val:
        result.merge(check_under_review_gate(schema, entity_id, entity_type, life_stage_val,
                                             db_path=db_path, caller=caller))

    # Gate 3 — Done Working: execution review doc must have a completed verdict
    if life_stage_val:
        result.merge(check_done_working_gate(schema, entity_id, entity_type, life_stage_val,
                                             db_path=db_path, caller=caller))

    # Commit gate: check entity folder is committed before under_review
    if life_stage_val:
        result.merge(check_commit_gate(schema, entity_id, entity_type, life_stage_val,
                                        db_path=db_path, caller=caller))

    # Brief presence: warn if project-grouping types lack a brief.md (soft advisory)
    if life_stage_val and entity_id:
        result.merge(check_brief_presence(schema, entity_type, entity_id, db_path=db_path))

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

PRIORITY_MAP = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "urgent": "critical",
}


def _parse_attr_pairs(attr_args):
    """Parse --attr key=value arguments."""
    pairs = []
    for item in (attr_args or []):
        if "=" not in item:
            print(f"Invalid --attr '{item}'. Expected key=value")
            sys.exit(1)
        k, v = item.split("=", 1)
        pairs.append((k.strip(), v.strip()))
    return pairs


def _add_common_flags(parser):
    """Add dimensional and attribute flags shared by create and update subcommands."""
    parser.add_argument("--focus", default=None)
    parser.add_argument("--life-stage", default=None, dest="life_stage")
    parser.add_argument("--resolution", default=None)
    parser.add_argument("--assessment", default=None)
    parser.add_argument("--importance-tactical", default=None, dest="importance_tactical")
    parser.add_argument("--health", default=None)
    parser.add_argument("--importance-strategic", default=None, dest="importance_strategic")
    parser.add_argument("--phase", default=None)
    parser.add_argument("--priority", default=None)
    parser.add_argument("--due", default=None)
    parser.add_argument("--attr", action="append", default=[])


def main():
    parser = argparse.ArgumentParser(
        description="Validate a Substrate operation against the schema"
    )
    sub = parser.add_subparsers(dest="operation")

    # create subcommand — same flags as create-entity.py
    create_p = sub.add_parser("create", help="Validate a create operation")
    create_p.add_argument("--type", required=True, dest="entity_type")
    create_p.add_argument("--name", required=True)
    create_p.add_argument("--description", default=None)
    _add_common_flags(create_p)

    # update subcommand — same flags as update-entity.py
    update_p = sub.add_parser("update", help="Validate an update operation")
    update_p.add_argument("entity_id")
    update_p.add_argument("--name", default=None)
    update_p.add_argument("--description", default=None)
    update_p.add_argument("--caller", choices=["agent", "human"], default=None,
                          help="Caller type: 'agent' (hard ready-gate) or 'human' "
                               "(soft ready-gate with override file). Default: inferred "
                               "from SUBSTRATE_AGENT env var (set=agent, unset=human).")
    _add_common_flags(update_p)

    args, remainder = parser.parse_known_args()

    if not args.operation:
        parser.print_help()
        sys.exit(1)

    # Load schema
    substrate_path = os.environ.get(
        "SUBSTRATE_PATH",
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    )
    db_path = os.path.join(substrate_path, "_system", "index", "substrate.db")
    schema = load_schema(substrate_path)

    extra_attrs = _parse_attr_pairs(args.attr)

    # Parse relationship args from remainder (same logic as create/update-entity.py)
    relationships = []
    i = 0
    while i < len(remainder):
        arg = remainder[i]
        if arg.startswith("--") and i + 1 < len(remainder):
            rel_name = arg[2:]
            if rel_name in schema.inverses:
                relationships.append((rel_name, remainder[i + 1]))
                i += 2
                continue
        print(f"Unknown argument: {arg}")
        sys.exit(1)

    # Build dimensions dict from flags
    dimensions = {}
    for dim in schema.dimension_names:
        val = getattr(args, dim, None)
        if val is not None:
            dimensions[dim] = val

    # Handle --priority alias
    if args.priority and "importance_tactical" not in dimensions:
        mapped = PRIORITY_MAP.get(args.priority.lower())
        dimensions["importance_tactical"] = mapped or args.priority

    # Validate
    if args.operation == "create":
        result = validate_create(
            schema, args.entity_type,
            name=args.name,
            description=args.description,
            dimensions=dimensions,
            relationships=relationships,
            extra_attrs=extra_attrs,
            due=args.due,
            db_path=db_path,
        )
        result.print_report(f"create {args.entity_type} \"{args.name}\"")

    elif args.operation == "update":
        # Infer caller: explicit flag > SUBSTRATE_AGENT env var > default 'agent'
        caller_arg = getattr(args, "caller", None)
        if caller_arg:
            caller = caller_arg
        elif os.environ.get("SUBSTRATE_AGENT"):
            caller = "agent"
        else:
            caller = "human"
        result = validate_update(
            schema, args.entity_id,
            dimensions=dimensions,
            relationships=relationships,
            extra_attrs=extra_attrs,
            db_path=db_path,
            caller=caller,
        )
        result.print_report(f"update {args.entity_id}")

    sys.exit(0 if result.valid else 1)


if __name__ == "__main__":
    main()
