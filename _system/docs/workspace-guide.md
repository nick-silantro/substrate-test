---
name: Workspace Guide
startup_for: [L0, L1, L2]
context_audience: [all]
---
# Workspace Guide

Operational rules and conventions that complement CLAUDE.md. CLAUDE.md provides the structural map (folders, scripts, skills); this document provides the principles that govern how you work.

## Operational Doctrine

- **Objects persist, Work resolves; some things are both.** Status is dimensional — HIP dimensions for objects, FLAIR dimensions for work, both for hybrids. Each type picks which dimensions it uses. See the Philosophy Quick Reference context-doc for the full dimensional model.
- **No retyping.** Entities don't change type through lifecycle. If A produces B, they're separate entities linked by a relationship.
- **All work is represented as work.** Any modification to Substrate is tracked through work-nature entities. Objects are modified as side effects of tasks; the task is the coordination surface.
- **One task = one agent.** Tasks are atomic. Multi-agent work decomposes into multiple tasks under a ticket.
- **Every change must work through Surface.** Substrate has two interaction paths: CLI/agents and the Surface UI. Any modification to scripts, schema, flags, or conventions must account for both. The test: would a non-technical user operating entirely through Surface experience the same correct behavior as an agent on the command line?
- **Be resourceful before asking.** Read the file. Check the context. Search for it. Then ask if you're stuck.
- **Systematization is a first-class lens, not a tiebreaker.** At problem framing and at review, ask: is there a pattern here that, if generalized, would prevent a whole category of problems rather than just this one?

## Folder Philosophy

**builds/ vs entity folders:** `builds/{name}/` holds the runnable codebase and its operational docs (CLAUDE.md, DESIGN.md, PATTERNS.md). Engagement docs (brief, doctrine, trace, reviews) belong in the project entity folder under `entities/project/`.

**Folders serve the system, not human navigation.** The directory structure exists because sharding requires it, entity types require separate namespaces, or scripts expect a specific path. Before creating a folder, ask: "does the system require this?" not "where would a human look?" Do not create ad-hoc directories. Content belongs in entity folders; temporary work goes to `staging/`.

**If a structural operation can't be accomplished through a script, that's a gap in the tooling — not a reason to go manual.** Flag it, add it to the bulletin board, and use the manual workaround only as a temporary bridge.

## NARRATIVE Rules

The NARRATIVE context-doc is the primary narrative context document — current focus, workspace state, guardrails, active threads. It is not entity state; it's the framing that makes entity data meaningful.

**Anti-pattern:** NARRATIVE must never list ticket UUIDs, enumerate completed tickets, or track individual entity progress. If an update reads like a query result or session changelog, it doesn't belong. Describe what changed and why it matters — not what was closed.

**Self-test before writing any Active Threads entry:** "Does this sentence name a specific entity, UUID, ticket, or completion event?" If yes, rewrite it.
- Good: "The invoicing schema is the current execution target."
- Bad: "`41388d4c` is at the BSC gate."

**Second self-test:** "Does this sentence describe what should happen next, or what is currently true?" Entries must describe state, not prescribe actions. Capture *why something matters*, not *how or when to address it*.
- Good: "Top-of-pyramid clarity is needed because agent routing depends on it."
- Bad: "Rambling sessions to come."

Action-framed entries create a nagging effect at session start — worse when the action was already completed between sessions.

**At session end:** Review NARRATIVE against what happened this session. Even if no obvious change occurred, check whether anything shifted that should be reflected. Propose updates if needed.

## Context Stack Architecture

Context documents come from two sources: engine files (in `_system/docs/` with frontmatter) and workspace entities (context-doc type). Both are queried via `substrate context context-stack`.

**Engine files** — structural reference, same for all users. Routing declared in YAML frontmatter (`startup_for`, `context_audience`). Cannot be edited by users.

**Workspace entities** — live state, instance-specific. NARRATIVE, USER, Bulletin Board. Routing declared in `meta.yaml`. Users can read and edit these.

Query your context stack: `substrate context context-stack` (defaults to L0) or `substrate context context-stack L1`.

### Referencing Context Documents

Three rules govern how context-docs are referenced throughout the system:

1. **Boot query in agent definitions only.** Each agent's definition file runs `substrate context context-stack {level}` exactly once. This is the only place the context-stack command is invoked.
2. **Name references everywhere else.** Level rules, heartbeats, skills, and entity content refer to context-docs by name: "see the Agent Operations context-doc." Never hardcode a file path.
3. **Routing via attributes, not code.** To change an engine doc's routing, update its frontmatter. To change a workspace entity's routing, update `startup_for` in its `meta.yaml`.

Name references are stable across file moves. Attribute queries are stable across routing changes.
