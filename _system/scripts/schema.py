#!/usr/bin/env python3
"""
Substrate schema loader.

Reads types.yaml, attributes.yaml, and relationships.yaml from _system/schema/
and provides structured access to type definitions, attribute specs, relationship
inverses, default statuses, and validation data.

Uses an attribute-centric access model: attributes declare which types can use them
via access declarations (exclusive toggle + required/preferred gradations). Dimensions
use the same access syntax.

Usage:
    from schema import load_schema
    schema = load_schema()

    # Core type info
    schema.known_types         # set of all type names
    schema.nature(t)           # grouping nature: ['work'] or ['object']
    schema.type_grouping(t)    # grouping name for type t

    # Attribute access (new model)
    schema.blocks()            # dict of block_name -> block definition
    schema.block_attrs(b)     # dict of attr_name -> attr def for block b
    schema.all_attrs()        # dict of attr_name -> attr def from attributes section
    schema.attr_access(f)     # access declaration for attribute f (or None for universal)
    schema.access_level(item, type, kind) # resolve to required/preferred/optional/forbidden

    # Backward-compatible methods (computed from new model)
    schema.type_attrs(t)      # dict of attribute definitions for type t
    schema.forbidden_attrs(t) # list of forbidden attribute names for type t
    schema.dimension_config(t) # dict: dimension -> category for this type
    schema.dimension_defaults(t) # dict: dimension -> default value (non-disallowed only)
    schema.enum_values(t, f)   # allowed enum values for an attribute, or None

    # Relationships
    schema.inverses            # dict: relationship -> inverse
    schema.relationship_names  # set of all valid relationship names

    # Other
    schema.dimension_names     # list of all dimension names — schema-driven (HIP/FLAIR + meta_status + grouping-level dims)
    schema.grouping_types(g)   # list of types in grouping g, or None
    schema.known_groupings()   # set of all grouping names
    schema.resolve_target_type(t) # resolve type/grouping/array -> set of concrete types
    schema.types               # raw types.yaml data
    schema.attributes          # raw attributes.yaml data
    schema.relationships       # raw relationships.yaml data

Requires: pyyaml (pip3 install pyyaml)
"""

import os
import yaml


class SubstrateSchema:
    """Loaded and indexed schema data."""

    def __init__(self, types_data, attributes_data, relationships_data):
        self.types = types_data
        self.attributes = attributes_data
        self.relationships = relationships_data

        # Derived indexes
        self._build_known_types()
        self._build_inverses()
        self._build_relationship_names()
        self._build_relationships_by_category()
        self._build_dimension_names()
        self._build_attr_index()
        self._build_access_index()

    def _build_known_types(self):
        """Set of all defined type names."""
        self.known_types = set(self.types.get("types", {}).keys())

    def _build_inverses(self):
        """Bidirectional relationship -> inverse mapping."""
        self.inverses = {}
        categories = self.relationships.get("categories", {})
        for cat_data in categories.values():
            rels = cat_data.get("relationships", {})
            for rel_name, rel_def in rels.items():
                inv = rel_def.get("inverse")
                if inv:
                    self.inverses[rel_name] = inv
                    # Only add reverse mapping if not already claimed
                    if inv not in self.inverses:
                        self.inverses[inv] = rel_name

    def _build_relationship_names(self):
        """Set of all valid relationship names (forward + inverse)."""
        self.relationship_names = set()
        categories = self.relationships.get("categories", {})
        for cat_data in categories.values():
            rels = cat_data.get("relationships", {})
            for rel_name, rel_def in rels.items():
                self.relationship_names.add(rel_name)
                inv = rel_def.get("inverse")
                if inv:
                    self.relationship_names.add(inv)

    def _build_relationships_by_category(self):
        """Build three category-keyed dicts:
          - relationships_by_category: all names (forward + inverse) — for validation
          - forward_relationships_by_category: child-side names (e.g. belongs_to) — for upward traversal
          - inverse_relationships_by_category: parent-side names (e.g. contains) — for downward traversal
        """
        self.relationships_by_category = {}
        self.forward_relationships_by_category = {}
        self.inverse_relationships_by_category = {}
        categories = self.relationships.get("categories", {})
        for cat_name, cat_data in categories.items():
            all_names = set()
            forward_names = set()
            inverse_names = set()
            rels = cat_data.get("relationships", {})
            for rel_name, rel_def in rels.items():
                forward_names.add(rel_name)
                all_names.add(rel_name)
                inv = rel_def.get("inverse")
                if inv:
                    inverse_names.add(inv)
                    all_names.add(inv)
            self.relationships_by_category[cat_name] = all_names
            self.forward_relationships_by_category[cat_name] = forward_names
            self.inverse_relationships_by_category[cat_name] = inverse_names

    def _build_dimension_names(self):
        """List of all dimension names from the schema."""
        dims = self.attributes.get("dimensions", {})
        self.dimension_names = list(dims.keys())

    def _build_attr_index(self):
        """Build unified attribute index from universal, blocks, and attributes sections."""
        self._universal_attrs = set(self.attributes.get("universal", {}).keys())
        self._block_defs = self.attributes.get("blocks", {})
        self._attr_defs = self.attributes.get("attributes", {})

        # Map attribute name -> where it lives ('universal', 'block:name', or 'attribute')
        self._attr_source = {}
        for f in self._universal_attrs:
            self._attr_source[f] = "universal"
        for block_name, block_def in self._block_defs.items():
            for f in block_def.get("fields", {}):
                self._attr_source[f] = f"block:{block_name}"
        for f in self._attr_defs:
            self._attr_source[f] = "attribute"

        # Warn on invalid storage tier values. schema-add-attribute.py validates
        # at ingress, but hand-edits to attributes.yaml bypass that — catch typos
        # like `storage: "colum"` at load time so they don't silently become
        # `indexed` (the default fallback in _resolve_storage).
        import sys as _sys
        for attr_name, fdef in self._attr_defs.items():
            storage = fdef.get("storage")
            if storage is not None and storage not in self._STORAGE_VALID:
                print(
                    f"[schema] warning: attribute '{attr_name}' has unknown "
                    f"storage value {storage!r}. Expected one of "
                    f"{sorted(self._STORAGE_VALID)}. Falling back to 'indexed'.",
                    file=_sys.stderr,
                )

    def _build_access_index(self):
        """Pre-compute access levels for all (attribute/block/dimension, type) pairs.

        This is the core of the access model. Resolution order:
        1. forbidden.types > forbidden.groupings > required.types > required.natures >
           required.groupings > preferred.types > preferred.natures > preferred.groupings >
           exclusive default
        """
        # Build nature lookup for efficiency
        self._type_nature = {}
        for t in self.known_types:
            self._type_nature[t] = self.nature(t)

        # Build grouping lookup for efficiency (mirrors _type_nature)
        self._type_grouping_cache = {}
        for t in self.known_types:
            self._type_grouping_cache[t] = self.type_grouping(t)

    def _resolve_access(self, access_decl, type_name, grouping_name=None):
        """Resolve an access declaration for a specific type.

        Args:
            access_decl: access declaration dict from YAML
            type_name: entity type name
            grouping_name: optional grouping name for the type (used for grouping-level dims)

        Returns: 'required', 'preferred', 'optional', or 'forbidden'.
        """
        if access_decl is None:
            return "optional"

        exclusive = access_decl.get("exclusive", False)
        nature = self._type_nature.get(type_name)
        # Use provided grouping_name or fall back to cached lookup
        gname = grouping_name if grouping_name is not None else self._type_grouping_cache.get(type_name)

        # Check forbidden.types (dimensions only)
        forbidden = access_decl.get("forbidden", {})
        if type_name in forbidden.get("types", []):
            return "forbidden"

        # Check forbidden.groupings — before any positive access grants
        if gname and gname in forbidden.get("groupings", []):
            return "forbidden"

        # Check required.types
        required = access_decl.get("required", {})
        if type_name in required.get("types", []):
            return "required"

        # Check required.natures (overlap: any shared nature matches)
        if nature and set(nature) & set(required.get("natures", [])):
            return "required"

        # Check required.groupings
        if gname and gname in required.get("groupings", []):
            return "required"

        # Check preferred.types
        preferred = access_decl.get("preferred", {})
        if type_name in preferred.get("types", []):
            return "preferred"

        # Check preferred.natures (overlap: any shared nature matches)
        if nature and set(nature) & set(preferred.get("natures", [])):
            return "preferred"

        # Check preferred.groupings
        if gname and gname in preferred.get("groupings", []):
            return "preferred"

        # Default based on exclusive toggle
        if exclusive:
            return "forbidden"
        return "optional"

    # --- Attribute access model methods ---

    def blocks(self):
        """Dict of block_name -> block definition."""
        return dict(self._block_defs)

    def block_attrs(self, block_name):
        """Dict of attr_name -> attribute definition for a block."""
        block_def = self._block_defs.get(block_name, {})
        return dict(block_def.get("fields", {}))  # 'fields' is the YAML key — not renamed here

    def all_attrs(self):
        """Dict of attr_name -> attribute definition from the attributes section."""
        return dict(self._attr_defs)

    def attr_access(self, attr_name):
        """Get the access declaration for an attribute or block attribute.

        Returns dict with 'exclusive', 'required', 'preferred' keys.
        Returns None for universal attributes (no access restrictions).
        """
        source = self._attr_source.get(attr_name)
        if source is None:
            return None  # unknown attribute
        if source == "universal":
            return None  # universal attributes have no access restrictions
        if source == "attribute":
            return self._attr_defs[attr_name].get("access")
        if source.startswith("block:"):
            block_name = source[6:]
            block_def = self._block_defs[block_name]
            # Check if attribute has its own access (narrows block access)
            attr_def = block_def["fields"][attr_name]
            if "access" in attr_def:
                return attr_def["access"]
            # Use block-level access
            block_access = {}
            for key in ("exclusive", "required", "preferred", "forbidden"):
                if key in block_def:
                    block_access[key] = block_def[key]
            return block_access
        return None

    def access_level(self, item_name, type_name, item_kind="attribute"):
        """Resolve an item's access level for a specific type.

        Works for attributes (including block attributes) and dimensions.

        Args:
            item_name: attribute name or dimension name
            type_name: entity type name
            item_kind: 'attribute' or 'dimension'

        Returns: 'required', 'preferred', 'optional', or 'forbidden'.
                 None if the item is unknown.
        """
        if item_kind == "dimension":
            dim_def = self.attributes.get("dimensions", {}).get(item_name)
            if dim_def is None:
                return None
            return self._resolve_access(dim_def.get("access"), type_name)
        else:
            # Check if it's universal
            if item_name in self._universal_attrs:
                return "required"
            # Check attributes section
            if item_name in self._attr_defs:
                access_decl = self._attr_defs[item_name].get("access")
                return self._resolve_access(access_decl, type_name)
            # Check block attributes
            for block_name, block_def in self._block_defs.items():
                if item_name in block_def.get("fields", {}):
                    access_decl = self.attr_access(item_name)
                    return self._resolve_access(access_decl, type_name)
            # Resolve dotted paths against blocks (e.g., "recurrence.schedule_type")
            if "." in item_name:
                prefix, suffix = item_name.split(".", 1)
                if prefix in self._block_defs:
                    # Validate suffix against the attribute's declared sub_attrs.
                    # The prefix attribute (e.g., "recurrence") has a complex data
                    # type with known sub-attributes listed in attributes.yaml.
                    block_attrs = self._block_defs[prefix].get("fields", {})
                    attr_def = block_attrs.get(prefix, {})
                    known_subs = attr_def.get("sub_attrs", [])
                    if known_subs and suffix not in known_subs:
                        return None  # unknown sub-attribute
                    return self.access_level(prefix, type_name, "attribute")
            return None  # unknown attribute

    # --- Backward-compatible methods (computed from new model) ---

    def type_attrs(self, type_name):
        """Get attribute definitions for a type.

        Returns attributes where access_level is 'required' or 'preferred' (not
        universal, not optional). This threshold prevents non-exclusive attributes
        like owner, due, version from appearing on every type.

        Backward compat: old type_attrs set is a SUBSET of the new set.
        The only additions are the 5 recurrence block attributes on work/both types.
        """
        result = {}

        # Add block attributes where type has required or preferred access
        for block_name, block_def in self._block_defs.items():
            block_access = {}
            for key in ("exclusive", "required", "preferred", "forbidden"):
                if key in block_def:
                    block_access[key] = block_def[key]
            level = self._resolve_access(block_access, type_name)
            if level in ("required", "preferred"):
                for fname, fdef in block_def.get("fields", {}).items():
                    result[fname] = fdef

        # Add attributes where type has required or preferred access
        for fname, fdef in self._attr_defs.items():
            access_decl = fdef.get("access")
            level = self._resolve_access(access_decl, type_name)
            if level in ("required", "preferred"):
                result[fname] = fdef

        return result

    def forbidden_attrs(self, type_name):
        """Get forbidden attribute names for a type.

        Computed from the exclusivity model: any exclusive attribute where this type
        is not in required or preferred is forbidden.

        Returns a list of attribute names (not definitions).
        """
        forbidden = []

        # Check attributes section
        for fname, fdef in self._attr_defs.items():
            access_decl = fdef.get("access")
            level = self._resolve_access(access_decl, type_name)
            if level == "forbidden":
                forbidden.append(fname)

        # Check block attributes
        for block_name, block_def in self._block_defs.items():
            block_access = {}
            for key in ("exclusive", "required", "preferred", "forbidden"):
                if key in block_def:
                    block_access[key] = block_def[key]
            block_level = self._resolve_access(block_access, type_name)
            if block_level == "forbidden":
                for fname in block_def.get("fields", {}):
                    forbidden.append(fname)

        return forbidden

    def is_immutable(self, attr_name):
        """Check if an attribute is marked immutable in the schema.

        Immutable attributes cannot be changed after entity creation.
        Currently checks the attributes section only (not blocks or dimensions).
        """
        if attr_name in self._attr_defs:
            return self._attr_defs[attr_name].get("immutable", False)
        return False

    def attr_default(self, attr_name, entity_type=None):
        """Get the default value for an attribute, or None.

        If entity_type is provided, checks type_defaults[entity_type] first
        before falling back to the global default.
        """
        # Check dimensions first (most callers ask for dimension defaults)
        dims = self.attributes.get("dimensions", {})
        if attr_name in dims:
            dim_def = dims[attr_name]
            if entity_type and "type_defaults" in dim_def:
                type_default = dim_def["type_defaults"].get(entity_type)
                if type_default is not None:
                    return type_default
            return dim_def.get("default")

        # Then check attribute defs
        if attr_name in self._attr_defs:
            fdef = self._attr_defs[attr_name]
            if entity_type and "type_defaults" in fdef:
                type_default = fdef["type_defaults"].get(entity_type)
                if type_default is not None:
                    return type_default
            return fdef.get("default")

        return None

    def enum_values(self, type_name, attr_name):
        """Get allowed enum values for an attribute, or None if not an enum.

        Searches type_attrs (required/preferred), then all attributes and blocks.
        """
        # Check type attributes first (backward compat path)
        attrs = self.type_attrs(type_name)
        attr_def = attrs.get(attr_name, {})
        if attr_def.get("data_type") == "enum":
            return attr_def.get("values", [])

        # Check all attributes (for optional attributes not in type_attrs)
        if attr_name in self._attr_defs:
            fdef = self._attr_defs[attr_name]
            if fdef.get("data_type") == "enum":
                return fdef.get("values", [])

        # Check block attributes
        for block_def in self._block_defs.values():
            if attr_name in block_def.get("fields", {}):
                fdef = block_def["fields"][attr_name]
                if fdef.get("data_type") == "enum":
                    return fdef.get("values", [])

        return None

    def is_list_attr(self, attr_name):
        """Check if an attribute is list-valued.

        An attribute is list-valued if:
          - data_type == "list" (plain list of strings), OR
          - list == true (list variant of another type, e.g., enum with list: true)

        Returns True if list-valued, False otherwise.
        """
        if attr_name in self._attr_defs:
            fdef = self._attr_defs[attr_name]
            if fdef.get("data_type") == "list":
                return True
            if fdef.get("list") is True:
                return True
        return False

    def list_attr_config(self, attr_name):
        """Get configuration for a list attribute.

        Returns a dict with:
          - is_list: True/False
          - max_items: int or None
          - enum_values: list of valid values (if enum-list) or None
          - has_column: True if attribute has a SQLite column (storage: indexed or column)
          - is_indexed: True if attribute has a CREATE INDEX (storage: indexed only)

        Returns None if not a list attribute.
        """
        if attr_name not in self._attr_defs:
            return None
        fdef = self._attr_defs[attr_name]
        is_list = fdef.get("data_type") == "list" or fdef.get("list") is True
        if not is_list:
            return None
        enum_values = None
        if fdef.get("data_type") == "enum" and fdef.get("list") is True:
            enum_values = fdef.get("values", [])
        return {
            "is_list": True,
            "max_items": fdef.get("max_items"),
            "enum_values": enum_values,
            # Resolved storage-tier booleans. Use `has_column` to decide whether
            # SQLite has a column at all (indexed or column tier), and
            # `is_indexed` to decide whether there's a CREATE INDEX on it.
            "has_column": self._has_column(fdef),
            "is_indexed": self._is_indexed(fdef),
        }

    def all_list_attrs(self):
        """Return names of all list-valued attributes."""
        result = []
        for attr_name, fdef in self._attr_defs.items():
            if fdef.get("data_type") == "list" or fdef.get("list") is True:
                result.append(attr_name)
        return result

    def indexed_list_attrs(self):
        """Return names of list attributes that should have a CREATE INDEX.

        Narrower than columned_list_attrs(): includes only storage: indexed
        (the default), not storage: column. Used by migrate-to-sqlite.py to
        decide which list-attribute columns get an index.

        An attribute is considered indexed if either:
          - `storage: indexed` is set (declarative form, also the default), or
          - Legacy `index_in_sqlite: true` is set (still honored).
        """
        result = []
        for attr_name, fdef in self._attr_defs.items():
            if fdef.get("data_type") == "list" or fdef.get("list") is True:
                if self._is_indexed(fdef):
                    result.append(attr_name)
        return result

    # --- Storage tier model ---
    #
    # Each attribute in the `attributes:` section has a storage tier that
    # determines how it lives in SQLite:
    #
    #   "indexed"   — (default) SQLite column + CREATE INDEX. Queryable, fast.
    #   "column"    — SQLite column, no index. Queryable, unindexed.
    #                  For fields that must be queryable in principle but are
    #                  rarely filtered (e.g., description, JSON blobs).
    #   "file_only" — meta.yaml only. No SQLite column. Not queryable via SQL.
    #                  For long-form prose fields where SQL filtering is
    #                  semantically meaningless (agenda, thesis, observed).
    #
    # Legacy: `index_in_sqlite: true` maps to "indexed". `index_in_sqlite: false`
    # is silently ignored — it does NOT downgrade to "column" or "file_only".
    # Under the new model, use the `storage` field for any non-default tier.

    _STORAGE_INDEXED = "indexed"
    _STORAGE_COLUMN = "column"
    _STORAGE_FILE_ONLY = "file_only"
    _STORAGE_VALID = {_STORAGE_INDEXED, _STORAGE_COLUMN, _STORAGE_FILE_ONLY}

    @classmethod
    def _resolve_storage(cls, fdef):
        """Return the storage tier string for an attribute definition.

        Precedence (first match wins):
          1. Explicit `storage: <tier>` in the YAML.
          2. Legacy `index_in_sqlite: true` → "indexed".
          3. Default → "indexed".

        Legacy `index_in_sqlite: false` is silently ignored (does NOT set file_only).
        Unknown storage values are silently treated as the default to avoid crashing
        callers; schema-add-attribute.py validates tier at ingress.
        """
        storage = fdef.get("storage")
        if storage in cls._STORAGE_VALID:
            return storage
        if fdef.get("index_in_sqlite") is True:
            return cls._STORAGE_INDEXED
        return cls._STORAGE_INDEXED

    @classmethod
    def _is_indexed(cls, fdef):
        """True if the attribute has a CREATE INDEX (storage: indexed)."""
        return cls._resolve_storage(fdef) == cls._STORAGE_INDEXED

    @classmethod
    def _has_column(cls, fdef):
        """True if the attribute has a SQLite column at all (indexed or column tier)."""
        return cls._resolve_storage(fdef) != cls._STORAGE_FILE_ONLY

    def is_indexed_attr(self, attr_name):
        """Return True if the named attribute has a CREATE INDEX.

        Returns False for unknown attributes and for attributes outside the
        `attributes:` section (universal/block/dimension are handled by
        dedicated code paths in migrate-to-sqlite.py).
        """
        fdef = self._attr_defs.get(attr_name)
        if fdef is None:
            return False
        return self._is_indexed(fdef)

    def has_column(self, attr_name):
        """Return True if the named attribute has a SQLite column.

        Includes both "indexed" and "column" tiers; excludes "file_only".
        """
        fdef = self._attr_defs.get(attr_name)
        if fdef is None:
            return False
        return self._has_column(fdef)

    def columned_scalar_attrs(self):
        """Return names of non-list attributes that have a SQLite column.

        Includes both "indexed" and "column" tiers. Excludes "file_only".
        Used by migrate-to-sqlite.py to derive ALTER TABLE statements.
        """
        result = []
        for attr_name, fdef in self._attr_defs.items():
            if fdef.get("data_type") == "list" or fdef.get("list") is True:
                continue
            if self._has_column(fdef):
                result.append(attr_name)
        return result

    def columned_list_attrs(self):
        """Return names of list attributes that have a SQLite column."""
        result = []
        for attr_name, fdef in self._attr_defs.items():
            if fdef.get("data_type") != "list" and fdef.get("list") is not True:
                continue
            if self._has_column(fdef):
                result.append(attr_name)
        return result

    def indexed_scalar_attrs(self):
        """Return names of non-list attributes with storage: indexed.

        Narrower than columned_scalar_attrs(): excludes "column" tier too.
        Used by migrate-to-sqlite.py to decide which columns get a CREATE INDEX.
        """
        result = []
        for attr_name, fdef in self._attr_defs.items():
            if fdef.get("data_type") == "list" or fdef.get("list") is True:
                continue
            if self._is_indexed(fdef):
                result.append(attr_name)
        return result

    def dimension_config(self, type_name):
        """Get dimension -> category mapping for a type.

        Computed from per-dimension access declarations.
        Returns dict like {'focus': 'preferred', 'health': 'disallowed', ...}

        IMPORTANT: Returns 'disallowed' (not 'forbidden') for backward
        compatibility. validate.py checks for 'disallowed' by exact string.
        """
        config = {}
        dims = self.attributes.get("dimensions", {})
        for dim_name, dim_def in dims.items():
            access_decl = dim_def.get("access")
            level = self._resolve_access(access_decl, type_name)
            # Map 'forbidden' to 'disallowed' for backward compat
            if level == "forbidden":
                level = "disallowed"
            config[dim_name] = level
        return config

    def dimension_defaults(self, type_name):
        """Get default dimensional values for a type (non-disallowed dimensions only).

        Respects type_defaults: if a dimension has a type_defaults entry for this
        type, that value takes precedence over the global default.

        Returns dict like {'focus': 'idle', 'life_stage': 'backlog', ...}
        """
        config = self.dimension_config(type_name)
        defaults = {}
        for dim_name, category in config.items():
            if category == "disallowed":
                continue
            default_val = self.attr_default(dim_name, entity_type=type_name)
            if default_val:
                defaults[dim_name] = default_val
        return defaults

    def dimension_values(self, dimension_name):
        """Get the valid values for a dimension.

        Assessment has dual value sets (delivery_values, outcome_values) that
        share "not_assessed". Deduplicate while preserving order — the dual
        sets are preserved in YAML for future automation that needs to
        distinguish delivery vs outcome context.
        """
        dim_def = self.attributes.get("dimensions", {}).get(dimension_name, {})
        if "values" in dim_def:
            return dim_def["values"]
        # Assessment has two value sets — deduplicate preserving order
        seen = set()
        vals = []
        for v in dim_def.get("delivery_values", []) + dim_def.get("outcome_values", []):
            if v not in seen:
                seen.add(v)
                vals.append(v)
        return vals

    def assessment_values(self, resolution):
        """Get the valid assessment values for a given resolution state.

        Returns delivery_values when the entity is unresolved, outcome_values
        for all resolved states (completed, cancelled, deferred, superseded).
        "not_assessed" appears in both sets and is always valid.

        Args:
            resolution: the entity's current resolution value (string)
        """
        dim_def = self.attributes.get("dimensions", {}).get("assessment", {})
        if resolution == "unresolved":
            return dim_def.get("delivery_values", [])
        return dim_def.get("outcome_values", [])

    # --- Type/grouping methods ---

    def type_grouping(self, type_name):
        """Get the grouping for a type."""
        type_def = self.types.get("types", {}).get(type_name, {})
        return type_def.get("grouping")

    def nature(self, type_name):
        """Get the grouping nature for a type as a single-element list.

        Returns ["work"] or ["object"]. Dual-nature groupings are not permitted.
        Handles legacy string format for backward compatibility during reads.
        """
        grouping_name = self.type_grouping(type_name)
        if not grouping_name:
            return None
        grouping_def = self.types.get("groupings", {}).get(grouping_name, {})
        raw = grouping_def.get("nature")
        if raw is None:
            return None
        # Normalize: string -> list for backward compat with legacy format
        if isinstance(raw, str):
            return [raw]
        return raw

    def required_relationships(self, type_name):
        """Get required relationships for a type, or empty list."""
        return self.relationships.get("required_relationships", {}).get(type_name, [])

    def grouping_types(self, grouping_name):
        """Get the list of types in a grouping, or None if not a grouping."""
        grouping_def = self.types.get("groupings", {}).get(grouping_name)
        if grouping_def:
            return grouping_def.get("types", [])
        return None

    def known_groupings(self):
        """Set of all grouping names."""
        return set(self.types.get("groupings", {}).keys())

    @staticmethod
    def is_grouping_ref(value):
        """Check if a target_type value is a grouping reference (group:name)."""
        return isinstance(value, str) and value.startswith("group:")

    @staticmethod
    def parse_grouping_ref(value):
        """Extract the grouping name from a grouping reference. Returns None if not a ref."""
        if isinstance(value, str) and value.startswith("group:"):
            return value[6:]  # len("group:") == 6
        return None

    def resolve_target_type(self, target):
        """Resolve a target_type value to a set of concrete type names.

        Handles three formats:
          - Type name:      "person"        -> {"person"}
          - Grouping ref:   "group:actors"  -> {"person", "user", "agent", "organization"}
          - Array:          ["user", "group:actors"] -> union of resolved elements

        Returns a set of type name strings.
        Raises ValueError if a string is not a known type or valid grouping reference.
        """
        if isinstance(target, list):
            result = set()
            for t in target:
                result.update(self.resolve_target_type(t))
            return result
        # Check for grouping reference (group:name)
        grouping_name = self.parse_grouping_ref(target)
        if grouping_name is not None:
            types = self.grouping_types(grouping_name)
            if types is not None:
                return set(types)
            raise ValueError(f"'group:{grouping_name}' references unknown grouping '{grouping_name}'")
        # Plain string — must be a known type
        if target in self.known_types:
            return {target}
        raise ValueError(f"'{target}' is not a known type (did you mean 'group:{target}'?)")


def _merge_user_schema(types_data, attributes_data, relationships_data, user_schema_dir):
    """Merge workspace schema-user/ extensions into engine schema data (in place)."""

    def _load_user(filename):
        path = os.path.join(user_schema_dir, filename)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    user_types = _load_user("types.yaml")
    if user_types:
        engine_type_dict = types_data.setdefault("types", {})
        engine_groupings = types_data.setdefault("groupings", {})
        # Add user type definitions
        engine_type_dict.update(user_types.get("types", {}))
        # Add new user groupings
        for g_name, g_def in user_types.get("groupings", {}).items():
            if g_name not in engine_groupings:
                engine_groupings[g_name] = g_def
        # For each user type, append it to its grouping's types list
        for t_name, t_def in user_types.get("types", {}).items():
            g_name = t_def.get("grouping")
            if g_name and g_name in engine_groupings:
                g_types = engine_groupings[g_name].setdefault("types", [])
                if isinstance(g_types, list) and t_name not in g_types:
                    g_types.append(t_name)

    user_attrs = _load_user("attributes.yaml")
    if user_attrs:
        attributes_data.setdefault("attributes", {}).update(
            user_attrs.get("attributes", {})
        )
        attributes_data.setdefault("blocks", {}).update(
            user_attrs.get("blocks", {})
        )

    user_rels = _load_user("relationships.yaml")
    if user_rels:
        engine_cats = relationships_data.setdefault("categories", {})
        for cat_name, cat_data in user_rels.get("categories", {}).items():
            if cat_name in engine_cats:
                engine_cats[cat_name].setdefault("relationships", {}).update(
                    cat_data.get("relationships", {})
                )
            else:
                import sys as _warn_sys
                print(
                    f"[schema] warning: user relationship category '{cat_name}' not found in engine schema — skipped",
                    file=_warn_sys.stderr,
                )


def load_schema(substrate_path=None):
    """
    Load all three schema YAML files and return a SubstrateSchema instance.
    Merges workspace schema-user/ extensions on top of the engine schema.

    Args:
        substrate_path: Root of the Substrate workspace.
                       Defaults to SUBSTRATE_PATH env var or auto-detected from script location.
    """
    if substrate_path is None:
        substrate_path = os.environ.get(
            "SUBSTRATE_PATH",
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        )

    engine_path = os.environ.get("SUBSTRATE_ENGINE_PATH", substrate_path)
    schema_dir = os.path.join(engine_path, "_system", "schema")

    def _load(filename):
        filepath = os.path.join(schema_dir, filename)
        if not os.path.exists(filepath):
            raise FileNotFoundError(
                f"Schema file not found: {filepath}\n"
                f"Engine path: {engine_path}\n"
                f"Set SUBSTRATE_ENGINE_PATH or run via the substrate CLI."
            )
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    types_data = _load("types.yaml")
    attributes_data = _load("attributes.yaml")
    relationships_data = _load("relationships.yaml")

    user_schema_dir = os.path.join(substrate_path, "_system", "schema-user")
    if os.path.isdir(user_schema_dir):
        _merge_user_schema(types_data, attributes_data, relationships_data, user_schema_dir)

    return SubstrateSchema(types_data, attributes_data, relationships_data)
