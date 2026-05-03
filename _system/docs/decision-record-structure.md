# Decision Record Structure

_How decision entities capture deliberative context — not just what was decided, but what was weighed and why._

_Created: 2026-04-09. Companion to `attribute-access-doctrine.md` and `schema-conventions.md`._

---

## What a Decision Entity Is

A `decision` entity is the deliberative record of a settled conclusion — the artifact that future agents and Nick can consult to answer: "Can I revisit this? Should I?" It covers any domain: system architecture, product choices, process design, personal commitments.

The key difference from a diary-entry or a trace: a decision entity is **indexable and linkable**. It `governs` the entities it constrains. It `supersedes` earlier decisions it overrides. The graph can walk from a product entity to the decision that shaped it.

### Phase Lifecycle

Decisions follow the same four-phase lifecycle as all object-nature entities:

- **forming** — the question is open; alternatives are still being considered; not yet binding
- **established** — the decision is made and operative; governs the entities it's linked to
- **aging** — still in effect but under pressure; conditions for revisit may be accumulating
- **retired** — overridden, reversed, or no longer relevant; may be `superseded_by` another decision or principle, but retirement has other valid causes (the thing it governed no longer exists; the situation changed; assumptions turned out wrong)

The phase is not an opinion about quality — it's a statement about current operative status. An aging decision is still binding; an agent should not act against it just because it's aging.

---

## Content File: `decision.md`

Every decision entity should have a `decision.md` content file. The file captures the deliberative record — not a summary of what happened, but the reasoning that future agents need to evaluate revisit risk.

### Template

```markdown
# [Decision Statement — what was decided, present tense, one sentence]

## What Was Decided

[One to three sentences. Precise enough that someone who wasn't in the session can implement
consistently with it. Avoid explaining why here — that's the Reasoning section.]

## Alternatives Considered

[Each alternative that was seriously weighed, and why it was rejected or deferred.
The goal: a future agent should be able to read this and not re-propose an alternative
that was already considered and rejected. If an alternative was dismissed quickly, say why.]

- **[Alternative A]:** [Why rejected]
- **[Alternative B]:** [Why deferred / conditions under which it becomes viable]

## Reasoning

[Why the chosen path over the alternatives. What made this the right call at this moment.
This section should be falsifiable — if a reader disagrees with the reasoning, they should
be able to point to a specific claim and argue against it.]

## Confidence and Known Uncertainties

**Confidence level:** high / medium / low

[What assumptions this decision rests on. What information would change the call.
If confidence is medium or low, explain what would need to be true for confidence to rise.]

## Questions Not Asked

[Acknowledged gaps — things that weren't investigated but might matter. Not a to-do list;
a record of where the deliberation was incomplete. Future agents use this to know where
the decision's foundations are thin.]

## Conditions for Revisit

[What would make this decision worth reopening. Specific and testable where possible.
Examples: "If X happens," "If the assumption about Y turns out to be wrong," "After Z months."]
```

### What to Skip

Not every decision needs every section. If there were no real alternatives (only one path was viable), say so in one line and move on. If confidence is high and assumptions are solid, the uncertainties section can be brief. The template is a ceiling, not a floor.

---

## The `system` Attribute

Decision entities have one type-exclusive boolean attribute: `system`.

```yaml
system: true
```

When set, it signals that this decision affects how Substrate itself works — its schema, scripts, relationships, operating model, or agent architecture. This enables flat-query scoping (`WHERE system = true AND phase = 'established'`) without requiring graph traversal.

**When to set it:** Any decision that changes or constrains Substrate's structure, operating conventions, or agent behaviors — regardless of what else it governs. A decision can be `system: true` and still `belongs_to` a user-domain project if it has cross-cutting implications.

**When not to set it:** Decisions about what you're building with Substrate (products, strategies, projects) — even if they're significant. The distinction is: does this decision tell Substrate agents how to operate, or does it tell the user what to build?

**Graph complement:** System decisions `belongs_to` the Substrate Evolution workstream or a ticket within it. The `system` attribute handles flat queries; the workstream handles execution context. The two work together — neither alone is sufficient.

---

## The Relationship Graph

A decision entity's power comes from its graph connections:

- **`governs` → [entities]** — The principles or constraints that flow from this decision to other entities. A decision about naming conventions `governs` the schema it applies to. A product architecture decision `governs` the product entity.
- **`governed_by` → [decisions/principles]** — What shaped this decision. A decision about file structure might be `governed_by` a principle about folder semantics.
- **`supersedes` → [earlier decision]** — If this decision replaces a prior one, link it explicitly. The superseded entity's phase should transition to `retired`, but supersession is one retirement path — not the only one.
- **`superseded_by` → [newer decision]** — Populated on the older entity when superseded.
- **`decided_by` → [person/agent]** — Who made this call. Can reference a person entity, an agent entity, or a diary-entry (the session where it was decided).

---

## The Meta-Layer vs. User-Domain Question

An earlier design question asked whether system-evolution decisions (about Substrate architecture) and user-work decisions (a PM's product choices) should share a type.

They do. The `system` boolean attribute handles scoping without a type split. The graph neighborhood provides additional context. There is no need for a separate type.

Why not a type split: types encode structural differences. System decisions and user decisions have identical structure — same deliberative record, same phase lifecycle, same relationships. The difference is domain and audience, not structure. Type splits on domain proliferate fast and make the type system into a categorization scheme.

Why not containment alone: containment-based scoping breaks for cross-cutting decisions (decisions that affect both Substrate's operating model and the user's work). The `system` attribute handles cross-cutting cleanly — set the flag regardless of what else the decision governs.

---

## Traces vs. Decisions

`_system/logs/traces/` are session-level prose records — what happened, in order. They are not indexed and not linkable. They're a journaling layer, not a knowledge layer.

Decision entities are the knowledge layer extraction: the one-sentence ruling, the reasoning, the known gaps. If a trace captured a significant decision, that decision should graduate to a decision entity. The trace remains as historical record; the decision entity becomes the navigable artifact.

---

_Reference: `attribute-access-doctrine.md` for attribute/dimension access model. `schema-conventions.md` for type definitions. `agent-operations.md` for how agents use decision entities during work._
