# Common Tasks

Each recipe shows what to say to your agent. The agent does the rest.

---

## Capture a note

> "Make a note that [your thought]."
> "Save this idea: [idea]."

The agent creates a note entity and stores it in your workspace. Good for quick captures you'll want to find later.

---

## Record a decision

> "We decided to [X]. Save that as a decision."
> "Log this decision: [what was decided and why]."

The agent creates a decision entity with what was decided, the reasoning, and alternatives considered. Decisions are permanent records — they don't get marked complete, they stay in your workspace as settled conclusions.

---

## Create a task

> "Create a task to [do X]."
> "Add a task: [title]. It's part of [project name]."

The agent creates a task and can link it to an existing project if you name one. You can also say "add a few tasks" and list them — the agent will create them in sequence.

---

## Mark something complete

> "Mark [task name] as done."
> "That's complete — close it out."

The agent finds the task and marks it resolved. If you're in the middle of a session working on something, you can also just say "done" and the agent will infer which task you mean.

---

## Find something

> "What did I note about [topic]?"
> "Find everything related to [project name]."
> "Show me all my open tasks."

The agent searches your workspace and returns matching entities. You can be specific ("find the decision about the API design") or broad ("what have I saved about marketing?").

---

## Log a meeting

> "We had a meeting with [names] about [topic]. Log it."
> "Log today's call with [name] — we discussed [topic] and decided [X]."

The agent creates a meeting entity and captures participants, topic, and any decisions or tasks that came out of it. You can be brief or detailed — the agent will ask follow-up questions if it needs more.

---

## Process files from staging

Drop any file (a PDF, a document, notes) into your `~/substrate/staging/` folder, then say:

> "Process my staging folder."
> "I dropped a file in staging — bring it in."

The agent inspects what's there, suggests what type of entity each file should become, and asks for confirmation before creating anything. The file moves out of staging once it's been processed.

---

## Archive old items

> "Archive [entity name]."
> "Clean up completed items from [project name]."

Archiving hides an entity from your workspace without permanently deleting it. It stays on disk for 30 days and can be restored if you change your mind. Say "restore [entity name]" to bring it back.

---

## Check workspace health

> "Validate my workspace."
> "Run a health check."

The agent checks that your database is consistent and all relationships are intact. This takes a few seconds and reports any issues it finds, along with how to fix them.

---

## Add a custom entity type

> "Add a new type called [name] — it's for [description]."
> "I want to track [things] — create a type for that."

The agent adds the new type to your schema so you can start using it immediately. Give it a singular name (e.g., "client" not "clients") and a brief description of what it represents.

---

**Next:** See [Entity Types](../reference/entity-types.md) for a full list of built-in types, or [Skills Catalog](../reference/skills-catalog.md) to understand what your agent can do automatically.
