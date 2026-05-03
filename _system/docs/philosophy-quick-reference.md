---
name: Philosophy Quick Reference
startup_for: [L0, L1, L2, "domain:graph"]
context_audience: [all]
---
# Philosophy Quick Reference

Condensed reference for agents needing deeper context. Source: `entities/document/3b/55/3b557a91-9d3c-417d-a1b6-e16f6648ba05/wdk-whitepaper-v1.md`

## Core Distinction

**Objects persist** — Products, systems, tools, people, horizons, planning artifacts. Have ongoing health and lifecycle.
**Work resolves** — Tasks, tickets, projects. Have completion conditions and outcomes.

The key distinction: objects are destinations and artifacts that work advances. Work is the pipeline that does the advancing. Horizons (pillars, missions, goals) are where you're aiming — not things you execute. Planning artifacts (decisions, inquiries, ideas) are knowledge produced through deliberation — not things you claim. Work to advance them is tracked as tickets; the objects themselves persist independently.

## Ontological Groupings

Each entity type belongs to a grouping. Each grouping has a nature — either `"work"` or `"object"` — which determines what status dimensions are available.

- **Actions** (work): task, ticket, chore
- **Efforts** (work): project, workstream, incident, initiative
- **Horizons** (object): pillar, mission, goal, milestone
- **Planning** (object): inquiry, idea, decision
- **Utility** (object): trigger, skill
- **Actors** (object): person, user, agent, organization
- **Knowledge** (object): note, document, reference, conversation, library
- **Artifacts** (object): article, script, product
- **Events** (object): meeting
- **Logs** (object): friction, diary-entry

### Grouping Rules

- **One nature per grouping.** Every type in a grouping shares the same nature. This is not a per-type decision; it's a per-grouping decision. If a type needs a different nature than its grouping, it belongs in a different grouping.
- **Groupings are rare.** They represent fundamentally different categories of being. Don't create a grouping for one type unless the category is real and future types would join it.
- **Default for new groupings: bias toward object.** Most things are objects. Work-nature should be reserved for things that have completion conditions — a clear pipeline endpoint.

### Nature Classification Guide

When deciding what nature a type needs, ask:

- **Does it get claimed and executed?** Does an agent pick this up, do work, and mark it done? If yes, it's work-nature.
- **Does it have ongoing lifecycle independent of any agent?** Does it grow, stabilize, decline, retire — regardless of whether anyone is working on it? If yes, it's object-nature.

The key test: if no agent is actively working on it, does it still exist and matter? Objects do. Work items might be abandoned or cancelled, but they don't *persist as artifacts*. A mission persists as a destination whether or not anyone is actively pursuing it. A ticket exists to be executed — it doesn't have ongoing existence beyond the pipeline.

## Status Dimensions

Status is not a single attribute. It is a set of independent dimensions, each answering a different question about an entity's state. Types pick which dimensions they use (the buffet model) — not every type needs every dimension.

### HIP Dimensions (available to object-nature groupings)

**Health** — How is this doing?
- growing, stable, declining, problematic, undefined

**Importance (Strategic)** — How central is this?
- core, important, peripheral

**Phase** — Where is this in its lifecycle?
- concept, in_development, testing, live, mature, legacy, retired

### FLAIR Dimensions (available to work-nature groupings)

**Focus** — Am I working on this right now?
- idle, active, waiting, blocked, paused, closed

**Life Stage** — Where is this in its workflow?
- backlog, ready, in_progress, under_review, done_working

**Assessment** — How is it going / how did it go?
- During delivery: not_assessed, on_track, at_risk, off_track
- After resolution: not_assessed, exceeded, succeeded, mixed, failed
- One dimension with two value sets; the active set depends on whether work is resolved.

**Importance (Tactical)** — How urgent is this?
- critical, high, medium, low

**Resolution** — Is this done?
- unresolved, completed, cancelled, deferred, superseded

### Hybrid types get both

Types in hybrid groupings (horizons, planning) can draw from both HIP and FLAIR dimensions. A mission might use Phase (HIP) + Focus (FLAIR) + Resolution (FLAIR). An inquiry might use Phase (HIP) + Resolution (FLAIR).

### Dimension Rules

- **Dimensions are permanent.** The 9 dimensions are defined by the philosophy and do not change: 3 HIP, 5 FLAIR, and 1 universal (`meta_status`). The HIP/FLAIR dimensions follow the buffet model — types select based on their nature. `meta_status` is universal: every entity has it regardless of type or nature. It is not part of the buffet model and has no named CLI flag; transitions are made via `--attr meta_status=VALUE`.
- **Bundled values are non-removable.** The values listed above ship with the system and cannot be deleted.
- **Bundled values are globally renamable.** A rename updates the schema and every entity that uses the old value. The rename is not complete until all references are migrated.
- **Users can add values to any dimension.** Custom values extend the vocabulary without breaking existing queries.
- **Per-type selection is where customization happens.** Each type declares which dimensions it uses and which values within those dimensions are valid for that type.
- **Values are universal.** The same value means the same thing everywhere it appears. No per-type aliases.

### Inter-Dimension Dependencies

Some dimensions interact with each other or with non-status fields:

- **Assessment** shifts value sets based on Resolution state. If Resolution moves to completed/cancelled/etc., Assessment switches from delivery values (on_track/at_risk/off_track) to outcome values (exceeded/succeeded/mixed/failed).
- **Focus: Blocked** is often derived from a relationship — the entity depends on something whose own state prevents progress. This may be reactive rather than manually set.
- **Temporal fields** (due date, start/end, duration) can imply status-like information. A meeting past its end time has effectively occurred. A task past its due date may be overdue. These are not dimensions but interact with dimensional queries.

## Work Hierarchy

Pillar (permanent) > Mission > Project > Ticket > Task > To-do (untracked)

The `>` denotes precedence, not containment. Relationship types vary by level:
- project `serves` mission (not `belongs_to` — projects are independent, not subsumed)
- ticket `belongs_to` project
- task `belongs_to` ticket
- project optionally `belongs_to` initiative (grouping only, not required)

- **Pillar** — Asymptotic ideal, never completable, orients decisions. Lagging proof: "If I achieved this, what would I point to as evidence?" Pillar diagnostics are things that happen *to* you or states of the world — not things you do. If it's under your control, it belongs at the mission level.
- **Mission** — Time-bound directional bet to advance a pillar. Leading indicators: "If events A, B, C happen, pillar metrics must improve." Missions are causal hypotheses connecting action to pillar advancement.
- **Project** — Bounded effort that ships outcomes. Where work actually happens. Serves missions via `serves` relationship — not contained by them.
- **Ticket** — Defined outcome requiring multiple steps. One ticket = one independently deliverable result. Belongs to a project.
- **Task** — Single action, single output. Belongs to a ticket.
- **Chore** — Standalone personal task, typically recurring. Does not require a parent ticket. First work type to break the formal hierarchy. Can optionally belong-to a workstream
- **To-do** — Checklist items inside tasks. Not formal entities

**The causal chain:** Projects advance mission trajectories → mission events cause pillar diagnostics to improve → pillar diagnostics confirm the vision is being realized. Measurement at each layer creates an explicit path from daily work to life vision.

## Project Subtypes

- **Campaign** — Time-boxed change effort. Advances mission targets
- **Ops Stream** — Renewable operational effort. Maintains SLO floors
- **Incident** — Emergency response to restore service

## Goal Types

| Type | Attachment | Purpose |
|------|-----------|---------|
| Ideal | Pillar | Asymptotic orientation |
| Floor | Pillar, Ops Stream | Do-not-breach guardrails |
| Threshold | Project, Mission | Ship/kill gates |
| Target | Mission | Committed end-state by deadline |
| Aim | Project, Ticket | Execution setpoint (overshoot to land on target) |

## No-Retyping Rule

Entities don't change type as part of an expected lifecycle. If entity A produces entity B, they are separate entities linked by a relationship.

- Wrong: "Research task is now a discovery project"
- Right: Research task (completed) -> enables -> Discovery project (new)

Changing your mind about what something IS (misclassified at creation) is different — just update the type.

## Agent Principles

**All work is represented as work.** Any modification to Substrate — even by orthogonal agents operating on objects — is tracked through a work-nature entity (task or ticket). The object is a side effect; the work entity is the coordination and concurrency surface.

**One task = one agent.** A task is the atomic unit of work, completed by a single agent. Multi-agent work decomposes into multiple tasks under a ticket. The ticket is the multi-agent coordination boundary.

**life_stage vs focus.** life_stage is durable workflow position (in_progress = work has begun). focus is transient attention state (active = agent is running right now). A task can be in_progress + idle (work started, no agent currently running).

For the full agent operations reference (concurrency control, claim protocol, level-specific knowledge): see the Agent Operations context-doc

## Purpose-Over-Type

Work belongs to its purpose, not its type. An article written for a project is a project deliverable — it belongs to the project, not to an article-production pipeline. The pipeline is infrastructure (how you produce it); the project is purpose (why it exists). When deciding where work belongs, ask: what is this in service of? That's the parent.

The corollary: don't let entity type drive organization. A document, a task, and a decision can all be project deliverables. They belong together under the project because purpose unites them, not because they're the same kind of thing.

## Relationship Heuristics

**The independence test:** If an entity would be meaningless without its parent, it `belongs_to`. If it could stand alone and just happens to touch something, it `relates_to`. When in doubt: "Would someone exploring the parent expect to find this?"

**Relationships are atemporal.** A relationship is a structural connection between entities, not an event record. The fact that A `produces` B doesn't mean production has occurred — it means that's the structural relationship between them, whether at planning time, mid-execution, or after completion. When information about timing matters, it belongs on entity fields (due dates, created timestamps, completion dates), not in the relationship name. Past-tense relationship names (`delivered`, `reported`, `completed`) are a design smell — they imply a moment in time that the relationship itself cannot represent.

## Engagement Modes

Orthogonal capabilities — describe how you relate to work, not what the work is. Stored as `engagement_mode` on all work-nature entities. Immutable after creation. Default: `none`.

Successors may be modes or entity types; entity types are marked *(entity)*.

- **wander** — Surface novelty without obligation. Maximum uncertainty tolerance. Output is entities (tickets, ideas, inquiries, decisions) produced during the wandering — not documents. Tasks may be added throughout ready and in_progress; the L1 decides when curiosity is satisfied. Requires: review doc (the "yes, and" response) + harvest gate (at least one outbound relationship) at under_review. → Explore, Idea *(entity)*, Inquiry *(entity)*, Decision *(entity)*, or Experiment
- **explore** — Directional sense-making with loose structure. Requires: trace, review doc (the review doc IS the findings doc — summary, recommendation, natural successors). → Execute or Decision *(entity)*
- **experiment** — Hypothesis-driven learning with controlled variables. Requires: hypothesis, trace. → Execute or Decision *(entity)*
- **lean** — Small, bounded execution where the work is self-evident. The task spec is the doctrine. No pre-work docs required; execute directly. Requires: review. Terminal — no mode successor.
- **execute** — Delivery under assumed known conditions. Requires: doctrine, plan, trace, review. Terminal — no mode successor.
- **none** — Mode not yet decided. No documentation requirements. Treat as a placeholder — assign a real mode before executing.

**Ready-gate:** When `life_stage` transitions to `ready`, precheck verifies that mode-appropriate documents exist in the entity folder (files whose names contain the required patterns). Gate behavior:
- Agent callers: hard gate — missing documents block the transition (error).
- Human callers: soft gate — missing documents produce a warning, and `_ready-gate-override.md` is written to the entity folder recording what was missing. The picking agent reads this file and either creates the missing document or confirms its absence is intentional.

Document presence is detected by filename pattern within the entity folder (e.g., any file containing `doctrine` satisfies the doctrine requirement). See the `engagement-pack` skill for the four-document system (doctrine, plan, examples, trace).

## Aggregation Types

Understanding how things group together:

- **Scale** — Same kind of thing at different granularity (tasks -> tickets)
- **Compositional** — Parts constitute a whole; remove a part and the whole changes
- **Instrumental** — Grouping in service of a purpose; contents replaceable, purpose persists
- **Contextual** — Things appearing together because context demands it (a lens, not a structure)
- **Evaluative** — Grouping to judge or analyze together; use "leads_to" not "belongs_to"

## Attribute Policy Categories

- **Required** — Must be present for valid entity state (validation)
- **Preferred** — Should be shown and top-of-mind (presentation)
- **Allowed** — Available but not prominent (presentation)
- **Disallowed** — Must not appear for this type (validation)
