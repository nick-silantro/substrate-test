---
name: context-sweep
description: Keep workspace context documents accurate. Run at session end, after a significant cluster of work, after a guardrail change, or when the user requests a context sweep.
version: 2.0.0
last_edited: 2026-05-10
---

# Context Sweep

Context documents drift by default — this skill provides a structured protocol for keeping them accurate.

---

## When to Invoke

- **Session end** — standard default
- **Cluster completion** — a significant group of work finished
- **Explicit request** — when the user requests a context sweep

Not triggered by completing a single ordinary action.

---

## Two Modes

**Light sweep** — Bulletin Board + NARRATIVE Active Threads only. The default.

**Full sweep** — All context docs in the workspace. The three covered in detail below (Bulletin Board, NARRATIVE, User) plus any others the workspace has. Use when something architecturally significant changed: design patterns established, guardrails changed, major decisions landed, or conventions confirmed.

When in doubt, run light.

---

## Finding Context Docs

Context docs are entities in the workspace graph. Resolve a file path before reading or writing:

```bash
substrate query find "Bulletin Board" --path --type context-doc
substrate query find "NARRATIVE" --path --type context-doc
substrate query find --type context-doc   # list all context docs in the workspace
```

The User context doc follows a `user-{name}` naming convention (e.g., `user-jane`). Find it with:

```bash
substrate query find "user" --type context-doc --path
```

If multiple results are returned, identify the one representing the workspace's primary user.

The `--path` output includes both the content `.md` file and a `.lock` file. Use the `.md` file — that is the editable content.

---

## Bulletin Board

**Find it:** `substrate query find "Bulletin Board" --path --type context-doc`

**Check for:**
- Items that became formal work (ticket,etc) → remove (the entity is the record now)
- Decisions that closed → remove or reduce to a one-line note
- Stale status lines → update or remove
- New unformed ideas from the session without a ticket home → add

**Rules:**
- Not a history document. Prune aggressively.
- Must stay under 200 lines. If approaching the limit, prune first — don't compress content to fit.
- If a thread is too large for the board but not yet a ticket: create a document entity and link from the board with a one-line status note. The thread content lives in the entity; the board retains only a pointer.

**Do not:** add session summaries, completed ticket lists, or entity UUIDs as primary content.

---

## NARRATIVE

**Find it:** `substrate query find "NARRATIVE" --path --type context-doc`

**Light sweep:** update Active Threads only — when a thread opened or closed this session.

**Full sweep:** update all sections.

**Check Focus:** does it reflect the current primary work mode and project?

**Check Workspace State:** any architecturally significant changes — confirmed patterns, deprecated approaches, resolved blockers?

**Check Active Threads:** threads that opened or closed; threads with stale descriptions.

**Rules (apply to every entry written):**
- Describe what IS true. Not what should happen next.
- Self-test 1: "Does this sentence name a specific entity, UUID, or completion event?" If yes, rewrite.
- Self-test 2: "Does this sentence describe current state or prescribe an action?" If action, rewrite.
- Must stay under 100 lines (target) / 200 lines (hard limit).

**Do not:** write "Ticket X was completed," enumerate finished work, or frame entries as next steps.

---

## User Context Doc

**Find it:** `substrate query find "user" --type context-doc --path`

**Full sweep only.** Update when something new is learned about the workspace's primary user through interaction — their working style, preferences, background, or current circumstances. Do not speculate; record what has been observed or stated.

**Check for:**
- New roles, responsibilities, or circumstances worth persisting
- Confirmed behavioral patterns or stated preferences
- Changes to current focus or goals

**Do not:** record ephemeral session details, repeat what's already there, or write anything that reads as a negative judgment.

---

## Other Context Docs

**Find them:** `substrate query find --type context-doc`

A workspace may have additional context docs beyond the three above — domain references, operational guides, preference docs, and others. During a full sweep, check each one for staleness.

**For each:**
- Does anything from this session make it inaccurate? → update
- Has it grown unwieldy? → prune or split
- Is it still relevant? → if the domain it covers no longer exists, consider archiving it

No special rules — just accuracy. Apply the same "describe what IS true" principle as NARRATIVE.

---

## Anti-Patterns

- **Accumulation without pruning.** Resolved Bulletin Board threads never removed. NARRATIVE describing a workspace that no longer exists.
- **Action-framed NARRATIVE entries.** "We should clean this up" instead of "The schema has two conflicting conventions that haven't been reconciled."
- **Entity ID pollution.** Entity names and UUIDs don't belong in prose context documents.

---

## Quick Checklist

**Light sweep:**

Bulletin Board:
- [ ] Any items that became work entities this session? → remove
- [ ] Any decisions that closed? → remove or compress to one line
- [ ] Any new unformed ideas without a home? → add
- [ ] Under 200 lines?

NARRATIVE — Active Threads:
- [ ] Any threads that opened this session? → add
- [ ] Any threads that resolved? → remove or update
- [ ] Each entry describes state (not action), names no UUIDs?

**Full sweep (in addition to above):**

NARRATIVE — other sections:
- [ ] Focus still accurate?
- [ ] Workspace State reflects current architecture?

User context doc:
- [ ] Anything new learned about the workspace user worth persisting?

Other context docs:
- [ ] Any doc made inaccurate by this session's work? → update
- [ ] Any doc that has grown stale or irrelevant? → prune or archive
