# Skills

Skills are pre-loaded expertise that tell your agent how to handle specific tasks correctly. Think of them as manuals the agent has already read — consulted automatically whenever they're relevant.

You don't need to invoke a skill directly. You describe what you want; the agent figures out which skill applies and uses it.

## What this looks like in practice

You drop a PDF into your `~/substrate/staging/` folder and say:

> "Process my staging folder."

The agent reads the file, identifies what kind of entity it should become, shows you a summary with a proposed name and type, and waits for your confirmation before creating anything. It knows to do all of this because the staging intake skill defines that workflow. None of that logic is something you had to teach it.

The same pattern holds for other tasks. Say:

> "Add a type called `client` for tracking clients."

The agent knows to extend the schema, store the new type in the right place, and confirm what it added. The schema evolution skill covers the whole workflow: what to validate, where to write the change, and how to describe the result back to you.

This consistency is the point. Without skills, the agent would handle each request from scratch, with no guaranteed approach. With skills, the expertise is permanent — you never have to explain how archiving works, what the difference between a task and a ticket is, or how staging intake should proceed.

## What comes with Substrate

Substrate ships with a set of built-in skills covering the core workflows: entity management, search and query, relationship linking, staging intake, archiving, schema customization, and workspace validation. The full list — what each skill does and what phrases trigger it — is in the [Skills Catalog](../reference/skills-catalog.md).

When Substrate updates, skills are typically updated along with it — your agent's built-in expertise improves without any action on your part.

---

**Next:** [How-To Guides](how-to-guides.md) shows common tasks in recipe form. [Skills Catalog](../reference/skills-catalog.md) has the full reference — what each skill does, and what phrases trigger it.
