# Updates and Background Services

Substrate runs two background services and checks for updates automatically. You don't manage any of this directly — it all happens behind the scenes.

## What's Running in the Background

Two services start automatically when your computer starts:

**Entity-watcher** monitors your workspace folder for file changes and keeps the internal database synchronized. When your agent creates or updates an entity, the watcher detects the change and updates the search index. This is what makes queries fast and accurate. If the watcher stops running, search results may go stale and entities may not appear where you expect them.

**Evaluate-triggers** runs on a regular schedule and handles automated workspace events: recurring tasks coming due, dependent tasks unlocking when a prerequisite completes, and similar background processing. It runs silently and requires no interaction.

Both services were set up automatically during installation. On Mac they run via launchd, on Linux via systemd, and on Windows via Task Scheduler. They start when your computer starts and stop when it shuts down.

## Getting Updates

At the start of each session, the agent checks whether a new version of Substrate is available. If there is one, it tells you:

> "There's a Substrate update available. Want me to run it?"

Say yes and the agent runs `substrate update`. This downloads the latest engine and refreshes your skills — usually completing in under a minute. Everything you've captured in your workspace (notes, tasks, decisions, projects) is untouched. Your custom schema extensions, aliases, and display preferences also survive the update unchanged. Only the engine itself is replaced.

If an update fails for any reason, the agent tells you and the previous version stays in place. Substrate does not leave your workspace in a broken state.

Say no and the agent will ask again at the start of your next session. You never have to remember to check for updates yourself. You can also run `substrate update` directly at any time to check and apply the latest version.

## When Something Seems Wrong

If Substrate is behaving unexpectedly — search returning odd results, entities not saving, something just feeling off — ask the agent:

> "Validate my workspace."

The agent runs a full health check: it compares what's on disk against the database, verifies that all relationships between entities are intact, and confirms that entity types and attributes follow the schema. Most issues are fixed automatically. If something needs your judgment, the agent explains what it found and what your options are.

If a background service has stopped running, the agent can diagnose it and give you the specific restart command for your platform. You don't need to know the difference between launchd and systemd. Just tell the agent the service seems down.

For platform-specific restart commands and technical details on each service, see [Background Services](../reference/background-services.md).

---

**Next:**
- [How-To Guides](how-to-guides.md) — common tasks with step-by-step instructions
- [Day-to-Day Use](day-to-day.md) — what a normal working session looks like
