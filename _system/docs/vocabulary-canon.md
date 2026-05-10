---
name: Vocabulary Canon
description: Authoritative definitions for all Substrate terms. When this document and any other disagree, this document is correct.
startup_for: [L0, L1, "domain:graph"]
context_audience: [all]
---
# Vocabulary Canon

Authoritative definitions for the core terms describing entity data in Substrate. When this document and any other document disagree, this document is correct and the other is terminology debt.

For relationship categories, folder naming, file naming, and query language: `.claude/skills/system-evolution/references/terminology.md`

## Terms

**Attribute** — Any named data point on an entity. The umbrella term. Everything defined in `attributes.yaml` is an attribute: universal attributes (id, name, type, etc.), block attributes (recurrence, completion_count, etc.), standalone attributes (owner, tech_stack, etc.), and dimensions (focus, resolution, etc.). When in doubt, "attribute" is always correct.

**Dimension** — A status attribute. The 8+1 axes that describe an entity's state: the five FLAIR dimensions (focus, life_stage, assessment, importance_tactical, resolution), the three HIP dimensions (health, importance_strategic, phase), and meta_status (universal). Dimensions are attributes with special semantics: enumerated values, nature-based buffet selection, and inter-dimension dependencies. Use "dimension" when referring to status specifically; use "attribute" when referring to entity data generally.

**Block** — A named group of semantically coupled attributes that share access rules. A composition mechanism, not a separate data concept. Members of a block are still attributes. Currently: the recurrence block. Use "block" when referring to the grouping mechanism; use "attribute" when referring to individual members.

**Relationship** — A structural edge between two entities. Relationships are not attributes. They are defined in `relationships.yaml` and follow the four-category taxonomy: hierarchical, causal, contextual ancestry, associative. Relationships appear in meta.yaml under their own keys (currently kebab-case, migrating to snake_case — see Naming below), separate from attributes.

**Field** — Reserved for UI presentation contexts. A field is what a user sees on a form: a label, an input, a dropdown. Use "field" when writing UI copy, form labels, or describing what appears on screen. Do not use "field" in schema definitions, documentation, agent prose, or system internals — use "attribute" or "dimension" instead.

**Property** — Dropped. Do not use in any context. Historical term with no stable definition in Substrate.

## Decision Rules

| Context | Use | Not |
|---------|-----|-----|
| Writing schema YAML comments | attribute, dimension | field, property |
| Writing documentation or skill prose | attribute, dimension | field, property |
| Writing agent output or system messages | attribute, dimension | field, property |
| Writing UI labels, form descriptions, tooltips | field | attribute, property |
| Referring to the recurrence group | block (for the group), attribute (for members) | field |
| Referring to belongs_to, enables, etc. | relationship | attribute, field, property |
| Referring to focus, resolution, life_stage, etc. | dimension (specific) or attribute (general) | field, property |

## Naming

All identifiers use `snake_case`: attribute names, dimension names, and relationship names. No exceptions.

Relationship names are currently kebab-case throughout the system (`belongs_to`, `relates_to`, `authored_by`). This is terminology debt — they should be snake_case (`belongs_to`, `relates_to`, `authored_by`) to match attribute naming. The structural migration is tracked separately from the terminology audit (ticket af76c26d).

## Known Terminology Debt

These existing files use terms inconsistent with this canon and were addressed by the terminology audit (ticket 667b55a6). They are resolved:

- `_system/schema/attributes.yaml` — the `fields:` section header is a structural migration (ticket pending), not a terminology sweep
- the Attribute Access Doctrine context-doc — prose and filename both updated
- `.claude/skills/schema-evolution/SKILL.md` — fixed
- `.claude/skills/onboarding/SKILL.md` — fixed
- `.claude/skills/system-evolution/references/decisions-log.md` — fixed
- the Philosophy Quick Reference context-doc — fixed
- the Schema Conventions context-doc — fixed

The following are structural migrations, not terminology sweeps:

- `_system/schema/relationships.yaml` — all relationship names use kebab-case, should be snake_case. Tracked in ticket af76c26d.
- All `meta.yaml` files — relationship keys use kebab-case, should be snake_case. Tracked in ticket af76c26d.
- All scripts that parse relationship names — handle kebab-case strings, need to handle snake_case after migration. Tracked in ticket af76c26d.
- `_system/schema/attributes.yaml` `fields:` section key — rename to `attributes:` and update all script readers (schema.py, validate_schema.py, etc.). Tracked in ticket 1726d43a. High risk: must be done as a coordinated rename + script update in one pass.
