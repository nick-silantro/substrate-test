#!/usr/bin/env python3
"""
Validate Substrate schema YAML files for internal consistency.

Runs semantic cross-file checks across types.yaml, attributes.yaml, and
relationships.yaml. Catches mismatches that a YAML parser alone cannot detect.

Usage:
    python3 validate_schema.py [SUBSTRATE_PATH]

Exit codes:
    0 = All checks passed
    1 = Validation errors found
"""

import os
import sys

from schema import load_schema


def _iter_all_attr_defs(schema):
    """Yield (source, attr_name, attr_def) for all attributes in attributes and blocks sections."""
    # Attributes section
    for fname, fdef in schema.attributes.get("attributes", {}).items():
        yield ("attributes", fname, fdef)
    # Block attributes
    for block_name, block_def in schema.attributes.get("blocks", {}).items():
        for fname, fdef in block_def.get("fields", {}).items():
            yield (f"blocks.{block_name}", fname, fdef)


def _iter_all_access_decls(schema):
    """Yield (source, item_name, access_decl) for all access declarations."""
    # Attribute access
    for fname, fdef in schema.attributes.get("attributes", {}).items():
        access = fdef.get("access")
        if access:
            yield ("attributes", fname, access)
    # Block-level access
    for block_name, block_def in schema.attributes.get("blocks", {}).items():
        access = {}
        for key in ("exclusive", "required", "preferred", "forbidden"):
            if key in block_def:
                access[key] = block_def[key]
        if access:
            yield (f"blocks.{block_name}", block_name, access)
    # Dimension access
    for dname, ddef in schema.attributes.get("dimensions", {}).items():
        access = ddef.get("access")
        if access:
            yield ("dimensions", dname, access)


def validate(schema):
    """Run all semantic checks. Returns list of error strings."""
    errors = []
    known_grouping_names = schema.known_groupings()

    # 1. Every type referenced in attribute/block access declarations exists in types.yaml
    for source, item_name, access in _iter_all_access_decls(schema):
        for level_key in ("required", "preferred", "forbidden"):
            level_decl = access.get(level_key, {})
            if isinstance(level_decl, dict):
                for t in level_decl.get("types", []):
                    if t not in schema.known_types:
                        errors.append(f"{source}.{item_name} access.{level_key} references unknown type '{t}'")
                for n in level_decl.get("natures", []):
                    if n not in ("work", "object"):
                        errors.append(f"{source}.{item_name} access.{level_key} references invalid nature '{n}'")

    # 2. Grouping membership: every type listed in a grouping exists
    groupings = schema.types.get("groupings", {})
    for g_name, g_def in groupings.items():
        for t in g_def.get("types", []):
            if t not in schema.known_types:
                errors.append(f"grouping '{g_name}' lists type '{t}' not found in types.yaml")

    # 3. Grouping coverage: every type appears in at least one grouping
    grouped_types = set()
    for g_def in groupings.values():
        grouped_types.update(g_def.get("types", []))
    for t in schema.known_types:
        if t not in grouped_types:
            errors.append(f"type '{t}' is not in any grouping")

    # 4. Reference target validity in fields and blocks
    for source, fname, fdef in _iter_all_attr_defs(schema):
        target = fdef.get("target_type")
        if target:
            targets = target if isinstance(target, list) else [target]
            for t in targets:
                grouping_name = schema.parse_grouping_ref(t)
                if grouping_name is not None:
                    if grouping_name not in known_grouping_names:
                        errors.append(f"{source}.{fname} references unknown grouping '{grouping_name}' in target_type 'group:{grouping_name}'")
                elif t not in schema.known_types:
                    errors.append(f"{source}.{fname} references unknown target_type '{t}'")

    # 5. Relationship target validity: target_types in required_relationships are valid
    for type_name, reqs in schema.relationships.get("required_relationships", {}).items():
        if type_name not in schema.known_types:
            errors.append(f"required_relationships defines rules for unknown type '{type_name}'")
        for req in reqs:
            for target in req.get("target_types", []):
                if target not in schema.known_types:
                    errors.append(f"required_relationships[{type_name}] references unknown target_type '{target}'")

    # 6. Inverse completeness: every inverse exists as a relationship name
    categories = schema.relationships.get("categories", {})
    all_rel_names = set()
    for cat_data in categories.values():
        for rel_name in cat_data.get("relationships", {}).keys():
            all_rel_names.add(rel_name)
    for cat_data in categories.values():
        for rel_name, rel_def in cat_data.get("relationships", {}).items():
            inv = rel_def.get("inverse")
            if inv and inv != rel_name and inv not in all_rel_names:
                all_inverses = set()
                for cd in categories.values():
                    for rd in cd.get("relationships", {}).values():
                        i = rd.get("inverse")
                        if i:
                            all_inverses.add(i)
                if inv not in all_inverses and inv not in all_rel_names:
                    errors.append(f"relationship '{rel_name}' has inverse '{inv}' not found as any relationship or inverse")

    # 7. Enum non-emptiness in universal, fields, and blocks
    for attr_name, attr_def in schema.attributes.get("universal", {}).items():
        if attr_def.get("data_type") == "enum" and not attr_def.get("values"):
            errors.append(f"universal.{attr_name} is enum but has no values")

    for source, fname, fdef in _iter_all_attr_defs(schema):
        if fdef.get("data_type") == "enum" and not fdef.get("values"):
            errors.append(f"{source}.{fname} is enum but has no values")

    # 8. Default validity: every default is in its enum's values list
    for attr_name, attr_def in schema.attributes.get("universal", {}).items():
        if "default" in attr_def and attr_def.get("data_type") == "enum":
            vals = attr_def.get("values", [])
            if attr_def["default"] not in vals:
                errors.append(f"universal.{attr_name} default '{attr_def['default']}' not in values {vals}")

    for source, fname, fdef in _iter_all_attr_defs(schema):
        if "default" in fdef and fdef.get("data_type") == "enum":
            vals = fdef.get("values", [])
            if fdef["default"] not in vals:
                errors.append(f"{source}.{fname} default '{fdef['default']}' not in values {vals}")

    # 9. Required relationship types exist in types.yaml
    for type_name in schema.relationships.get("required_relationships", {}).keys():
        if type_name not in schema.known_types:
            errors.append(f"required_relationships references unknown type '{type_name}'")

    # 10. Connection rule types exist
    for rule in schema.relationships.get("connection_rules", {}).get("restricted", []):
        for t in rule.get("source_types", []):
            if t not in schema.known_types:
                errors.append(f"connection_rules restricted rule references unknown source_type '{t}'")
        for t in rule.get("target_types", []):
            if t not in schema.known_types:
                errors.append(f"connection_rules restricted rule references unknown target_type '{t}'")

    # 11. no_same_type_nesting applies_to types exist
    nesting = schema.relationships.get("connection_rules", {}).get("no_same_type_nesting", {})
    for t in nesting.get("applies_to", []):
        if t not in schema.known_types:
            errors.append(f"no_same_type_nesting applies_to unknown type '{t}'")

    # 12. Groupings have exactly one nature: either "work" or "object" (dual-nature is not permitted)
    for grouping_name, grouping_def in schema.types.get("groupings", {}).items():
        nature = grouping_def.get("nature")
        if isinstance(nature, str):
            if nature not in ("work", "object"):
                errors.append(f"grouping '{grouping_name}' has invalid nature '{nature}' (must be exactly one of: 'work', 'object')")
        elif isinstance(nature, list):
            if not nature:
                errors.append(f"grouping '{grouping_name}' has empty nature array")
            elif len(nature) > 1:
                errors.append(f"grouping '{grouping_name}' has dual-nature {nature} — groupings must be either work or object, not both")
            elif nature[0] not in ("work", "object"):
                errors.append(f"grouping '{grouping_name}' has invalid nature value '{nature[0]}' (must be 'work' or 'object')")
        else:
            errors.append(f"grouping '{grouping_name}' has invalid nature type (expected list)")

    # 13. Attribute name collision check: no attribute in fields/blocks collides with universal
    universal_attrs = set(schema.attributes.get("universal", {}).keys())
    for source, fname, fdef in _iter_all_attr_defs(schema):
        if fname in universal_attrs:
            errors.append(f"{source}.{fname} collides with universal attribute name")

    # 14. Block attribute / attributes section collision check
    block_attr_names = set()
    for block_name, block_def in schema.attributes.get("blocks", {}).items():
        for fname in block_def.get("fields", {}):
            block_attr_names.add(fname)
    for fname in schema.attributes.get("attributes", {}):
        if fname in block_attr_names:
            errors.append(f"attributes.{fname} collides with a block attribute name")

    # 15. Every attribute in attributes section has an access declaration
    for fname, fdef in schema.attributes.get("attributes", {}).items():
        if "access" not in fdef:
            errors.append(f"attributes.{fname} is missing an access declaration")

    # 16. Every dimension has an access declaration
    for dname, ddef in schema.attributes.get("dimensions", {}).items():
        if "access" not in ddef:
            errors.append(f"dimensions.{dname} is missing an access declaration")

    # 17. Exclusive attributes/dimensions must have at least one type, nature, or grouping
    #     in required or preferred. Grouping-level dimensions use preferred.groupings —
    #     that counts as a valid positive access route.
    for source, item_name, access in _iter_all_access_decls(schema):
        if access.get("exclusive", False):
            has_targets = False
            for level_key in ("required", "preferred"):
                level_decl = access.get(level_key, {})
                if isinstance(level_decl, dict):
                    if level_decl.get("types") or level_decl.get("natures") or level_decl.get("groupings"):
                        has_targets = True
                        break
            if not has_targets:
                errors.append(f"{source}.{item_name} is exclusive but has no types, natures, or groupings in required/preferred")

    # 18. Grouping names in access declarations must reference known groupings
    for source, item_name, access in _iter_all_access_decls(schema):
        for level_key in ("required", "preferred", "forbidden"):
            level_decl = access.get(level_key, {})
            if isinstance(level_decl, dict):
                for g in level_decl.get("groupings", []):
                    if g not in known_grouping_names:
                        errors.append(f"{source}.{item_name} access.{level_key} references unknown grouping '{g}'")

    # 19. forbidden.types is invalid on attribute access declarations (dimension-only).
    #     forbidden.groupings is allowed on attributes — schema.py supports it at lines 191-193.
    for fname, fdef in schema.attributes.get("attributes", {}).items():
        access = fdef.get("access", {})
        forbidden_block = access.get("forbidden", {})
        if "types" in forbidden_block:
            errors.append(f"attributes.{fname} uses forbidden.types — only dimensions support per-type exclusions; use forbidden.groupings for attributes")
    for block_name, block_def in schema.attributes.get("blocks", {}).items():
        if "forbidden" in block_def:
            errors.append(f"blocks.{block_name} has 'forbidden' at block level (only dimensions support forbidden.types)")

    # 20. type_defaults validity: values must be valid enum members; types must exist in
    #     types.yaml; dimension must not be disallowed for the referenced type
    for dname, ddef in schema.attributes.get("dimensions", {}).items():
        type_defaults = ddef.get("type_defaults", {})
        if not type_defaults:
            continue
        valid_values = ddef.get("values", [])
        for type_name, default_val in type_defaults.items():
            if type_name not in schema.known_types:
                errors.append(
                    f"dimensions.{dname} type_defaults references unknown type '{type_name}'"
                )
            else:
                dim_config = schema.dimension_config(type_name)
                if dim_config.get(dname) == "disallowed":
                    errors.append(
                        f"dimensions.{dname} type_defaults[{type_name}] is unreachable: "
                        f"'{dname}' is disallowed for type '{type_name}'"
                    )
            if valid_values and default_val not in valid_values:
                errors.append(
                    f"dimensions.{dname} type_defaults[{type_name}] value '{default_val}' not in values {valid_values}"
                )

    # 21. Grouping-level dimension structural enforcement
    #     Any dimension with category: "grouping" must:
    #     (a) declare a grouping: key that names a known grouping
    #     (b) use preferred.groupings or required.groupings as its positive access route
    #         (not preferred.types/required.types — those are type-scoped, not grouping-scoped)
    for dname, ddef in schema.attributes.get("dimensions", {}).items():
        if ddef.get("category") != "grouping":
            continue
        # (a) grouping: key must be present and valid
        grouping_ref = ddef.get("grouping")
        if not grouping_ref:
            errors.append(f"dimensions.{dname} has category 'grouping' but is missing the 'grouping:' key")
        elif grouping_ref not in known_grouping_names:
            errors.append(f"dimensions.{dname} has category 'grouping' but grouping '{grouping_ref}' is not in types.yaml")
        # (b) access must route via groupings: not types:
        access = ddef.get("access", {})
        has_grouping_route = False
        uses_type_route = False
        for level_key in ("required", "preferred"):
            level_decl = access.get(level_key, {})
            if isinstance(level_decl, dict):
                if level_decl.get("groupings"):
                    has_grouping_route = True
                if level_decl.get("types"):
                    uses_type_route = True
        if not has_grouping_route:
            errors.append(
                f"dimensions.{dname} has category 'grouping' but has no preferred.groupings or required.groupings — "
                f"grouping-level dimensions must route access via groupings:, not types:"
            )
        if uses_type_route:
            errors.append(
                f"dimensions.{dname} has category 'grouping' but uses preferred.types or required.types — "
                f"use preferred.groupings instead (type-level scoping is outlawed for grouping dims)"
            )

    return errors


def main():
    substrate_path = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    try:
        schema = load_schema(substrate_path)
    except Exception as e:
        print(f"FATAL: Could not load schema: {e}")
        sys.exit(1)

    print(f"Loaded schema: {len(schema.known_types)} types, {len(schema.relationship_names)} relationships")

    errors = validate(schema)

    if errors:
        print(f"\n{len(errors)} validation error(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("All checks passed.")
        sys.exit(0)


if __name__ == "__main__":
    main()
