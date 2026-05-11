# Entities and the Graph

Everything you put into Substrate is an **entity** — a named, typed piece of information. A task is an entity. A decision is an entity. A person, a note, a meeting, a project — all entities. Each one has a name, a type, and a short description. Entities are the building blocks of your workspace.

## Two Kinds of Entities

Entities come in two fundamental kinds. Work entities need attention and completion — they move through a pipeline and get closed when finished. Object entities just need to exist and be findable — they persist indefinitely and accumulate context over time.

These aren't informal categories — they're formal schema concepts. In Substrate's schema, this distinction is called **entity nature** (work or object), and related types are further organized into **groupings** that cluster by how they're used. See [Entity Types](../reference/entity-types.md) for the full breakdown.

**Work entities** are things that happen and complete. They move through a staged progression and get resolved when finished. When you ask your agent to "create a task to follow up with Sarah," it creates a work entity with a status that your agent tracks until it's closed.

**Object entities** are things that exist and persist. A decision you made doesn't "complete" — it stays true until something changes it. A person in your network doesn't go away. A note, a document, a product you're building — these exist independently of any particular workflow and accumulate context over time.

## What Creating Entities Looks Like

You tell the agent what to capture. It builds the structure.

> "I just had a meeting with the design team. We decided to push the launch to June and I need to update the stakeholder doc."

From that one sentence, your agent creates:
- A `meeting` entity for today's design team call
- A `decision` entity: launch pushed to June
- A `task` entity: update the stakeholder doc

Each entity is connected — the task and decision both link back to the meeting they came from.

> "Add a task to finish the pricing section. It belongs to the website project."

The agent creates the task and sets the relationship. Now the project knows it contains this task, and the task knows which project it serves.

## How Entities Connect

Entities connect through typed **relationships**. A task *belongs to* a project. A decision *relates to* a product. A note *comes from* a meeting. Connections are bidirectional — if a task belongs to a project, the project knows it contains that task.

These connections let your agent answer questions a folder of files never could:

- "Show me everything connected to the website launch."
- "What decisions have we made that affect this project?"
- "Who is involved in anything I'm currently working on?"

Ask any of these and the agent searches the full graph — following connections, not just matching names.

## Why Structure Beats Folders

A folder of files stores information. The graph stores *meaning*.

When you drop a note into a folder, it sits there. When your agent creates a note entity and connects it to the meeting it came from, the project it supports, and the decision it influenced — that note becomes findable from multiple angles, not just by name or date.

You don't manage any of this yourself. You tell the agent what's happening, and the structure appears as a side effect of normal conversation.

The payoff compounds over time. Suppose in week one you captured a decision to use a particular vendor, connected to the meeting where you made the call. In week two you created a task referencing that vendor, linked to the project it belongs to. Three weeks later, you can ask "what's everything related to that vendor?" and get back the original decision, the task that followed, the meeting where it was discussed, and any notes connected along the way — all in one query. A folder of files would require you to remember every filename and search term. The graph just knows.

## What You'll Work With Day to Day

Most sessions involve a small core of entity types:

**Work entities** — have a status, move toward completion

| Type | What it is |
|------|-----------|
| `task` | A single action to complete |
| `ticket` | A formal work unit that produces a single reviewable result; suited for project work and team handoffs. Informal convention: tickets tend toward agent-delegated work, tasks toward things you do yourself — but this line isn't enforced. |
| `project` | A bounded effort with a goal and an end — ships, then closes |
| `chore` | A recurring personal task (weekly reviews, reminders) |

**Object entities** — persist and accumulate context

| Type | What it is |
|------|-----------|
| `note` | A freeform captured thought |
| `decision` | A settled conclusion that governs future work |
| `person` | Someone you work with or reference |
| `document` | A formal piece of written content |
| `meeting` | A conversation that produced outcomes |

You'll encounter more types as your workspace grows — and you can define your own. The full built-in list is in [Entity Types](../reference/entity-types.md).

Once entities exist, you can find them by name, search by topic or meaning, filter by status, or ask about everything connected to a project. Ask your agent "what have I captured about the website project?" and it pulls together tasks, decisions, notes, and meetings — anything linked to that project, regardless of when it was created.

## Next Steps

- See [Entity Types](../reference/entity-types.md) for the complete type reference.
- See [Day to Day](day-to-day.md) for how the agent manages entities in practice.
- See [Context Documents](context-documents.md) for how the agent uses your workspace knowledge to stay oriented.
