# Context Documents

Your agent doesn't guess what you're working on. At the start of every session, it loads a set of context documents — a snapshot of your workspace, a profile of you, and a running list of what needs attention. That's how it already knows where you left off.

## What They Are

Context documents come in two broad kinds. **Workspace context documents** live in your workspace, are specific to you, and you can read and edit them directly. Others are engine-managed — they load automatically as part of how Substrate works and aren't something you need to touch.

Three workspace context documents are created during setup:

**Your profile (USER-{name})** — who you are, how you prefer to work with AI, and whether you'd rather have technical details explained or handled silently. This document shapes how your agent communicates with you and works for you.

**Your workspace state (NARRATIVE)** — what you're currently focused on, what's actively moving, and the essential context an agent needs to be useful from the first message. Not a log of everything that happened — just what's true right now.

A well-maintained NARRATIVE might include: the main project and where it stands, a key constraint or decision that governs current work, and any threads that are paused but shouldn't be forgotten. It's short — a few paragraphs at most.

**Your bulletin board (BULLETIN-BOARD)** — a running surface for actionable but unformed items: work without a project home, open decisions, things that need to be picked up. Not a history — a dispatch surface. Items move off the board when they become tickets, when decisions close, or when threads resolve.

When you open your workspace in Claude Code, the agent loads all of these alongside any other relevant context, and queries your workspace directly. That's why it can pick up where you left off without you having to explain.

Advanced users can create additional context documents to load specific guidance into particular sessions.

---

## How They Stay Current

Context documents age. If NARRATIVE still describes work you finished two weeks ago, your agent is starting from stale information.

Keep them current in two ways:

**Ask the agent to update them.** When something meaningful shifts — you finished a major piece of work, started a new project, changed direction — say so: "Update the context to reflect that we're now focused on X." The agent revises the relevant document and shows you what changed. Or ask for a context sweep and it reviews everything at once, pruning what's stale and updating what's shifted.

**Edit them directly.** The files are plain text. Open them in any editor or through Claude Code, make the change, and save. The next session picks up the updated version immediately.

The NARRATIVE document works best when it describes current state, not recent history. "The main focus is closing out the client engagement before the board meeting" is useful. "We wrapped up three deliverables last Tuesday" is not — that's the past, and it crowds out what's actually true today.

## What They're Not

Context documents are not a complete record of everything you've ever done in Substrate. That's what entities are for — structured records of notes, decisions, projects, and tasks that persist indefinitely. Context documents are the short summary your agent needs to orient itself right now.

They're also not set-and-forget. A workspace that's been active for six months without a context update will feel like it's running from old news. Thirty seconds of "update context: we finished the client project and we're now building the website" is enough. Asking for a context sweep is even easier — the agent handles the rest.

## A Note on the Agent's Memory

It might feel like your agent "remembers" everything from past sessions. It doesn't, not the way a person does. What it has is a well-maintained snapshot — accurate context documents and a structured workspace full of your work. That's enough for it to pick up naturally where you left off. When context is stale, it shows: the agent starts answering from outdated information, which can look like confusion or missing knowledge, but the fix is usually a single update, not a technical repair.

---

**Next:**
- [Skills](skills.md) — what your agent already knows how to do, and how it applies that knowledge automatically
- [Day-to-Day Use](day-to-day.md) — what a working session looks like once context is set up
- [How-To Guides](how-to-guides.md) — common tasks in recipe form
