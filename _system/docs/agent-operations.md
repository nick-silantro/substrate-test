---
name: Agent Operations
description: How agents operate within Substrate — operational principles, dimensional semantics, and system coordination.
startup_for: [all]
context_audience: [all]
---
# Agent Operations Reference

How agents operate within Substrate. Covers operational principles, dimensional semantics, and system coordination.

For the philosophical framework (status dimensions, work hierarchy, groupings): `philosophy-quick-reference.md`
For agent definitions: `.claude/agents/`
For the context stack architecture and SHARP resolution: see Context Stack Architecture context-doc

## Core Principles

**All autonomous agent work is represented as work.** When agents modify Substrate autonomously — even orthogonal agents like knowledge-worker updating object relationships — that work is tracked through a work-nature entity (task or ticket). The object is modified as a side effect; the work entity is the coordination surface. This applies to pipeline work and heartbeat operations. Direct user instructions (a user asking the co-founder agent to create an entity in session) are exempt — the user interaction is itself the coordination event.

**Every change must work through Surface.** Substrate has two interaction paths: CLI/agents and the Surface UI. Any modification to scripts, schema, flags, or conventions must account for both. If a script flag changes, the Surface route that calls it must change too. If a script produces warnings on stdout, consider whether Surface users will ever see them. The test: would a non-technical user operating entirely through Surface experience the same correct behavior as an agent on the command line?

**When an agent makes a mistake, ask whether the system created the conditions for it.** Before attributing an error to agent failure, ask: did the tooling, documentation, or skill guidance make the wrong action easy and the right action unclear? If yes, the system should be fixed — not just the agent corrected. The test: would a well-intentioned agent following the available guidance have done the same thing? If yes, the guidance is the problem.

**Systematization is a first-class lens, not a tiebreaker.** Before proposing any solution, ask: is there a more systematic or self-scaling way to address the class of problem this represents — not just this instance? This question belongs at problem framing, not just when options are already on the table. The answer may not change the immediate work — it might instead seed a ticket, a schema generalization, or a new skill. The cost of asking is near zero; the leverage from catching a generalizable pattern early is high. Default to the more systematic approach when the effort is comparable.

**Solve the real problem, not the assigned one.** When given a task, distinguish between the requirement and the proposed solution. The user's request often contains both — "build me a webpage so Matt can pay by card" contains a requirement (Matt needs to pay by card easily) and a proposed solution (a webpage). If a better solution exists that fully satisfies the requirement with less friction, surface it before executing the proposed one. The test: can you state the underlying requirement independently of the solution? If yes, check whether the proposed solution is actually the best one.

## Script Flag Conventions

All relationship flags on Substrate scripts use snake_case: `--belongs_to`, `--depends_on`, `--relates_to`, `--serves`, `--authored_by`, etc. The old kebab-case format (`--belongs-to`) is no longer accepted and will produce an "Unknown argument" error.

## Agent Capabilities

### Semantic Search

Agents can find entities by meaning, not just by name. `query.py search` uses embedding-based similarity to match queries against entity names and descriptions.

```bash
# Find entities related to a concept
python3 _system/scripts/query.py search "agent coordination"

# Filter by type
python3 _system/scripts/query.py search "trading intelligence" --type project

# Machine-readable output for agent pipelines
python3 _system/scripts/query.py search "recurring maintenance" --format json
```

**When to use search vs find:** `find` matches by name substring — fast, exact, good for known entities. `search` matches by meaning — slower, approximate, good for discovery. Using `search` when you know the entity name will return semantically-adjacent noise instead of the intended entity. When in doubt, use `find` first.

**JSON output:** `--format json` returns structured results with id, type, name, description, and similarity score. Use this when search results feed into agent logic rather than human display.

**Setup:** Semantic search requires a one-time setup (`setup-search.py`). The `search` command will prompt to run this automatically on first use. Embeddings are rebuilt with `rebuild-embeddings.py` after bulk entity changes.

## Dimensional Semantics for Agents

Two dimensions are critical for agent coordination. They answer different questions and must not be conflated.

**life_stage** — Where is this in its workflow?
- Durable. Survives across sessions. A task stays `in_progress` even when no agent is running.
- This is the pipeline column — the kanban position.
- Agents find work via `query.py workable`. That filters for ready, unblocked, and unclaimed.
- Values: backlog, ready, in_progress, under_review, done_working
- **backlog:** work that exists but hasn't been committed to yet. No agent can or should pick it up — backlog progression is a human prioritization decision.
- **ready gate:** Two pre-ready quality checks exist. The agent BSC checks the plan for design gaps. When `user_check_required: true`, a human check is also required before ready.
- **under_review:** work awaiting review. A separate `user_review_required` flag signals when a user review is also needed — additive, not substitutive.

**focus** — Is someone working on this right now?
- Transient. Reflects current agent attention. When an agent session ends, focus should return to `idle`.
- `active` means an agent is awake and touching this right now. `in_progress` (life_stage) means work has begun but may not be actively attended.
- Values: idle, active, waiting, paused, closed

**The distinction matters:** A task can be life_stage=in_progress + focus=idle (work started, no one currently running). Or life_stage=in_progress + focus=active (agent is executing right now). Or life_stage=in_progress + focus=waiting (blocked on external input).

**resolution** — Is this done?
- Terminal state. Once resolved, work doesn't revert.
- Values: unresolved, completed, cancelled, deferred, superseded

## meta_status and Archiving

`meta_status` is a system-level flag — separate from the HIP/FLAIR dimensional model — that controls whether an entity is included in normal operations.

**Values:** `live` (default), `archived`

**`meta_status=archived` is a manual, intentional act.** It is set by a human or agent as a deliberate judgment call — never by the cascade system, never automatically. Archive is not a lifecycle stage. Completed work stays live; archiving is for entities you want permanently excluded from normal queries, pipelines, and operations.

**When to archive:** Test or toy entities created during development, entities created by mistake, work that was never real and should not appear in retrospectives or reports. Do not archive completed work simply because it is finished — the dimensional model preserves history while completed work remains live.

**Archiving is one-way in practice.** No system reverses `meta_status=archived`. An archived entity can be manually restored, but nothing in the pipeline or cascade system will do it automatically. Treat archive as permanent.

**Effect on queries:** `query.py workable` and most operational queries exclude archived entities by default. Archived entities are still accessible by UUID and appear in `query.py find` results unless filtered.

## System Lock

A coarse global lock that signals "shared infrastructure is being modified — all other agents should stand down." Backed by a `system_locks` SQLite table.

Use `system-lock.py acquire --agent NAME --description TEXT` before modifying shared infrastructure (`_system/scripts/`, `_system/schema/`, `.claude/skills/`, `_system/docs/`). Release with `system-lock.py release --agent NAME`. Check with `system-lock.py check` (exit 0 = clear, exit 1 = locked). Default TTL: 30 minutes; renew with `system-lock.py renew`.

Interactive sessions (human + agent in terminal) are exempt — the human presence is the coordination signal.

Full command syntax in the `system-lock` skill.

## Reactive Dimension Transitions

The transition system (`_system/scripts/cascades.py`) handles dependency-driven state changes automatically.

**Blocking at creation:** When an entity is created with `--depends_on` and the target is unresolved, the new entity's `is_blocked` attribute is set to `true` automatically.

**Unblocking on completion:** When an entity's `resolution` changes to `completed` or `superseded`, all dependents are checked. If a dependent's `is_blocked` is `true` and ALL of its dependencies are now resolved, `is_blocked` is cleared.

**Cancellation:** When a dependency is cancelled or deferred, dependents stay blocked. Use `query.py stuck` to surface entities permanently blocked by cancelled/deferred dependencies. These require human decision.

**Containment filtering:** `query.py workable` excludes entities whose ancestors are blocked, even if the entity itself is not. Containment blocking is enforced at query time.

**Cycle prevention:** Adding a `depends_on` relationship that would create a circular dependency is rejected with an error.
