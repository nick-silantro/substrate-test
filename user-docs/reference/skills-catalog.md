# Skills Catalog

This page is a reference for Substrate's built-in skills — what triggers each one and what you'll see when it runs. You don't need to invoke skills directly; the agent detects when one applies and uses it automatically.

---

## Entity Management

**Triggers when you say things like:** "Create a note about...", "Add a task to...", "Rename this project", "Mark that as done", "Delete this"

The agent creates, updates, and removes items in your workspace correctly every time — choosing the right entity type, filling in required details, connecting items to their parent projects, and keeping the internal database in sync. You describe what you want; the agent handles the structure.

When creating something, the agent typically confirms what it created — the name, type, and any connections it made — so you can see the result without having to ask.

---

## Entity Query

**Triggers when you say things like:** "Find everything about...", "What are my current tasks?", "Show me all decisions under this project", "What did I capture last week?"

The agent searches your workspace by name or by meaning, follows connections between items, and surfaces relevant results. It can navigate the full graph — showing all tasks under a project, or finding notes that relate to a topic even if they don't use the exact words.

Results come back as a list of matching entities, each with its name, type, and a brief description. For broad queries, the agent may group results by type or ask if you want to filter further.

---

## Relationship Management

**Triggers when you say things like:** "Link this to...", "Connect these two", "Move this task under a different project", "Show me what's related to..."

The agent creates and removes connections between items in your workspace. Every connection is bidirectional — if Task A belongs to Project B, Project B knows it contains Task A. The agent maintains this consistency automatically.

After linking items, the agent confirms the connection was made and which entities are now related.

---

## Staging Intake

**Triggers when you say things like:** "Process my staging folder", "What's in staging?", "Turn this file into an entity"

The staging folder (`~/substrate/staging/`) is a drop zone for files you want to bring into your workspace. Drop a PDF, a markdown file, or any document there, then ask the agent to process it.

The agent reads each file, suggests an entity type, and shows you what it found — a summary of the content and its proposed name and type. It waits for your confirmation before creating anything. Nothing gets written without your approval. Once confirmed, the file moves out of staging and the entity appears in your workspace connected to any related items.

---

## Archive Management

**Triggers when you say things like:** "Archive this", "I'm done with that project", "Restore the note I archived", "Clean up old items"

The agent archives items you no longer need active. Archived items disappear from normal searches and views but stay on disk for 30 days — you can restore them any time during that window. After 30 days they can be permanently removed.

The agent confirms what it archived and reminds you of the 30-day restore window. If you ask to permanently delete something, it asks for explicit confirmation before proceeding.

---

## System Validation

**Triggers when you say things like:** "Check my workspace health", "Something seems off", "Validate everything", "Repair any inconsistencies"

The agent runs a set of integrity checks: it verifies the database matches what's on disk, confirms all connections between items are intact, and checks that entity types and attributes follow the schema.

You see a report of what was checked and what was found — either a clean bill of health, a list of issues that were fixed automatically, or flagged items that need your judgment. The agent explains each issue in plain language before asking what to do.

---

## Schema Evolution

**Triggers when you say things like:** "Add a new type called...", "I want to track clients — create a client type", "Add a field for budget to projects", "Hide the types I never use", "I want to call 'decisions' something else"

The built-in entity types cover most needs, but you can extend the schema for your specific work. The agent adds new types, attributes, and relationship kinds to your workspace — local additions that don't affect anyone else. Built-in types remain unchanged; your extensions layer on top.

After each change, the agent confirms what was added or modified and shows you how to start using it immediately.

---

## Next Steps

- [Entity Types](entity-types.md) — the full list of built-in entity types and when to use each
- [Configuration](../learn/configuration.md) — workspace configuration options, including hiding types and creating aliases
- [How-To Guides](../learn/how-to-guides.md) — common tasks in recipe form
- [Skills](../learn/skills.md) — the mental model behind how skills work
