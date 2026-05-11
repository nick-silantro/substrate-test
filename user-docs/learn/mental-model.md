# What Is Substrate?

Substrate is a personal workspace that gives your AI assistant a persistent memory of your work. Decisions, projects, context, and ideas are organized so every session already knows what's going on. You stop re-explaining yourself and pick up where you left off.

**The mental model: AI that never makes you start from scratch.**

## How It Works

Substrate stores everything as **entities** — structured objects with a type and a name. A note is an entity. A task is an entity. A project, a decision, a person you work with — all entities. Each type captures what matters for that kind of thing.

Entities also connect to each other. A task belongs to a project. A decision governs a product. A note came from a meeting. These connections form a **knowledge graph** — a map of your work and how the pieces relate.

When you open a session, your AI assistant loads context documents — high-level summaries of where things stand. And when it needs to go deeper, the graph is right there: any fact, connection, or decision is a quick query away. Not loaded all at once — pulled in a split second when relevant, like a person thinking before they answer. You don't brief it. It's already oriented. After a call where you settled on a vendor, you capture that decision. Weeks later, a new session starts and the agent can surface the decision, which project it affects, and what tasks came out of it — no re-briefing required.

## What You Do

You don't manage the graph directly. You talk to your assistant.

Open your workspace in **Claude Code** — Anthropic's AI assistant — and describe what you want in plain language:

- "Create a note about the meeting I just had with Sarah"
- "What tasks are still open on the website project?"
- "Mark the logo review as done"
- "Add a decision: we're going with Stripe for payments"

The assistant handles everything — creating the entity, filing it correctly, connecting it to related work. You never touch a file or run a command.

Everything is stored locally on your computer. Nothing goes to the cloud. Your data stays yours.

## Two Kinds of Entities

Substrate distinguishes between two kinds of entities:

**Work entities** — tasks, tickets, projects, chores. These have a clear finish line. Work moves through a pipeline: it starts, progresses, and completes (or gets cancelled or deferred). When a task is done, it's resolved. The record stays, but it's closed.

**Object entities** — notes, decisions, people, products, documents, ideas. These don't complete; they accumulate context over time and persist in your workspace indefinitely. Your note about Sarah's feedback doesn't go away when the project ends. Your decision about Stripe is still there next month.

This distinction matters because your assistant tracks them differently. Work entities appear in your active work view and can be marked done, queued, or deferred. Object entities surface automatically when relevant — ask about a project and the related decisions and notes come with it. The distinction tells your assistant what is actionable versus what is reference material, so it can show you the right thing at the right time.

## What Makes It Work

The mental model only pays off if you actually use it. Three habits matter:

**Capture in the moment.** The system works because decisions, tasks, and context get recorded when they happen. A decision you meant to capture but forgot is a gap the agent can't fill. You don't need to be thorough — a sentence is enough. The agent handles structure.

**Describe, don't organize.** You never need to decide where something goes. "We moved the deadline to April" is sufficient. The agent creates a decision entity, connects it to the right project, and files it correctly. Your job is to say what happened. The agent's job is everything else.

**Using it is maintaining it.** Substrate doesn't require upkeep separate from using it. Every time you capture a decision or log a meeting, the knowledge grows. Every session the agent reads and updates what it knows. The workspace stays current because you're working in it.

## Next Steps

Substrate isn't useful until you've filled it in. Installation includes a guided setup where your assistant asks about your work — what you're building, what gets lost, and how you like to collaborate. Once that's done, the workspace is ready to use.

- [Your First Session](first-session.md) — how to ease in on day one
- [Entities and the Graph](entities-and-graph.md) — a deeper look at how entities and connections work
