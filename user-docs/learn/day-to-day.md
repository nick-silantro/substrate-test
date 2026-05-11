# Day to Day

Monday morning. You open your workspace in Claude Code and the agent is already oriented — it knows you're mid-sprint on the website project, that the logo review is still open, and that you decided last week to push the launch to June. You don't re-explain any of this. You just start.

## What a session looks like

You talk; the agent acts.

At the start of a session, you might just describe what happened:

> "I had a call with the team this morning. We decided to drop the mobile feature from v1 and push the launch to June."

The agent captures that and confirms: "Got it — I've recorded a decision to drop mobile from v1 and another to move the launch date to June. Both are linked to the website project." Next session, those facts are already there.

Other common patterns:

- **Capture a task**: "Create a task to follow up with Alex next week about the contract."
- **Find something you noted**: "What did I decide about the pricing model?"
- **Mark work complete**: "Mark the API design task as done."
- **Log a meeting**: "We met with the design team today. They signed off on the new nav structure."
- **Capture a quick thought**: "Make a note that we should revisit the onboarding flow after launch."
- **Maintain your workspace**: "Archive the completed items from last quarter" or "Run a health check."

You don't manage files, folders, or databases. You don't run commands. You speak in plain language about your work, and the agent builds the structure.

## What the agent already knows

At session start, the agent reads a set of context documents your workspace maintains: what you're currently focused on, which projects are active, recent decisions, and how you like to work.

This knowledge grows over time. After a few weeks, the agent knows not just your open tasks but the reasoning behind your decisions, who the relevant people are, and how the different pieces of your work connect. A new session at week eight feels less like opening a tool and more like resuming a conversation with a collaborator who has been paying attention.

## Between sessions

Two services run in the background. One watches your workspace for file changes and keeps the database in sync. The other handles periodic automated events. They start automatically when your computer starts and run silently.

When a session ends, there's nothing to save or close. Your workspace is already up to date. The next session starts exactly where this one left off.

## What to do when something feels off

If something seems out of sync — a task you marked done is still showing up, or the agent doesn't seem to know about a decision you captured — tell it:

> "Something seems wrong — can you check the workspace health?"

The agent can run a validation check and tell you what it finds.

---

**Next:**
- [Entities and the Graph](entities-and-graph.md) — what all this stored information actually looks like and how it connects
- [How-To Guides](how-to-guides.md) — common tasks in recipe form
