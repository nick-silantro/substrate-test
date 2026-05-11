# Entity Types

Every piece of information in Substrate is stored as an **entity** — a typed object with a name, description, and connections to other entities. Choosing the right type helps your agent track work correctly and connect related information.

Entities fall into two fundamental categories:

- **Work types** have a lifecycle. They move from created → in progress → done. Use these for things you act on.
- **Object types** persist indefinitely. Use these for things you reference, produce, or track over time.

---

## Work Types

### Actions — Atomic work items

| Type | When to use it |
|------|---------------|
| **task** | A single action item for personal tracking — "create a task to call Sarah," "add a task to finish the report." The everyday unit of getting things done. |
| **ticket** | A formal work unit that produces a single reviewable result. Use when work has clear deliverables and review steps — suited for project work and team handoffs. |
| **chore** | A standalone personal task, typically recurring. Use for things that repeat on a schedule — weekly reviews, recurring reminders. |

### Efforts — Bounded work with outcomes

| Type | When to use it |
|------|---------------|
| **project** | A container for related tickets, documents, and decisions. Use when a body of work has a defined goal and endpoint. |
| **workstream** | An ongoing, renewable stream of work with service-level commitments. Use for continuous responsibilities (e.g., "client support," "content publishing"). |
| **incident** | An emergency response effort to restore something broken. Use when something is on fire and needs focused resolution. |

---

## Object Types

### Horizons — Where you're aiming

| Type | When to use it |
|------|---------------|
| **pillar** | A permanent, never-fully-achievable ideal that orients all your decisions. Think: "I want to be a respected authority in my field." |
| **mission** | A time-bound directional bet that advances a pillar. Think: "Ship a public product by Q3." |
| **milestone** | A significant checkpoint worth tracking and celebrating — when the checkpoint itself matters, not just the tickets that deliver it. |

### Planning — Deliberation outputs

| Type | When to use it |
|------|---------------|
| **decision** | A settled conclusion — what you decided, what you weighed, and why. Use when the reasoning matters as much as the outcome. |
| **inquiry** | An open investigation where you're tracking a question until it's answered. |
| **idea** | A possibility worth holding onto — something you might build, pursue, or explore later. |

### Knowledge — Things to retain

| Type | When to use it |
|------|---------------|
| **note** | A freeform captured thought. The most general-purpose type. |
| **document** | Formal written content — a report, spec, proposal, or brief. |
| **reference** | An external resource you're keeping — a link, article, video, or post captured for future use. |
| **quote** | A verbatim extract from someone else, attributed to its source. |
| **framework** | A structured mental model for understanding a domain — not written content, not provisional, but a durable lens. |
| **principle** | A standing rule that governs future work — settled, authoritative, and durable. |
| **pain-point** | A documented problem experienced by a user, customer, or market segment. |
| **value-prop** | A benefit claim a product makes, typically framed in response to a pain point. |
| **audience** | A defined segment of people a product or message targets. |

### People

| Type | When to use it |
|------|---------------|
| **person** | Someone you reference — a contact, collaborator, or subject in your knowledge graph. |
| **organization** | A company, team, or group. |
| **user** | A human who operates within Substrate (typically just you). Created during onboarding. |

### Artifacts — Things you produce and ship

| Type | When to use it |
|------|---------------|
| **article** | A piece of written content intended for publication. |
| **script** | A structured outline or script for video or audio content. |
| **product** | A persistent thing you build, ship, and maintain. |
| **build** | A codebase or deployable artifact with its own repo and development lifecycle. |
| **resume** | A professional document produced in multiple formats, tailored per audience. |
| **video** | A produced video published or shared externally — the published work, not the raw file. |

### Events

| Type | When to use it |
|------|---------------|
| **meeting** | An intentional gathering to discuss and produce outcomes — scheduled or ad hoc. |

### Logs

| Type | When to use it |
|------|---------------|
| **correspondence** | An email, LinkedIn message, or other external communication sent or received. |

### Deliverables

| Type | When to use it |
|------|---------------|
| **invoice** | A formal payment request issued to an external party. |
| **receipt** | A payment confirmation or acknowledgment. |

### Resources

| Type | When to use it |
|------|---------------|
| **tool** | An external software tool, service, or platform you use in your work. |

### Opportunities

| Type | When to use it |
|------|---------------|
| **job-opportunity** | A tracked external job pursuit, from discovery through application and outcome. |

### Assets — Binary files

Assets are large binary files stored separately in your workspace's `assets/` folder.

| Type | When to use it |
|------|---------------|
| **logo** | A brand logo file — vector or raster. |
| **signature** | A personal signature image for documents and correspondence. |
| **video-file** | A raw video file (MP4, MOV, WebM). Distinct from a **video** entity, which represents the published work. |
| **audio-file** | An audio file (MP3, WAV, FLAC). |
| **thumbnail** | A preview or cover image for another entity. |
| **photo** | A photograph or image capture. |

---

## System Types

These types are managed by Substrate and your agent — you don't typically create them directly.

| Type | What it is |
|------|-----------|
| **context-doc** | A living document your agent reads at session start to stay oriented. Created during onboarding. |
| **skill** | A pre-configured expertise your agent uses to handle specific tasks. |
| **trigger** | An automation mechanism that fires actions in response to events. Not user-facing in this release. |
| **agent** | An AI actor that operates in your workspace. |

---

## What if nothing fits?

Start with **note**. You can always ask your agent to reclassify it later, or add a custom type if you track something that genuinely needs its own type.

---

- [Configuration](../learn/configuration.md) — add custom types, aliases, and hidden types
- [CLI Reference](cli.md) — the `substrate entity create` command for creating entities
- [Skills Catalog](skills-catalog.md) — how the agent handles entity creation automatically
- [How-To Guides](../learn/how-to-guides.md) — common tasks in recipe form
