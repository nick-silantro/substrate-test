# Substrate Release Notes

## 0.1.11

**Smarter session startup**

- Agents are now told that the Pending Updates document is part of the context stack — no grepping or extra commands needed. This removes unnecessary work at session start and keeps initialization fast.

## 0.1.10

**Cleaner first-session experience**

- Agents no longer surface raw CLI syntax to users. Substrate commands run behind the scenes; you speak in intentions and the agent handles the rest.

## 0.1.9

**Semantic search always on**

- Semantic search is now set up automatically during `substrate init` and `substrate update`. No separate opt-in step required.
- The embedding model is cached at `~/.substrate/model-cache/` so it only downloads once per machine.
- If setup fails (no network, model unavailable), the install or update still completes — run `substrate search setup` manually when you have a connection.

## 0.1.8

**Release notes formatting**

- Version headers in pending update notices are now H3, so multiple skipped versions each get a clean section without a redundant "What's new" label.

## 0.1.7

**Migration transparency**

- Pending update notices now warn you when an update includes workspace migrations, so you know before you say yes.
- Agents are now instructed to surface migration output explicitly after running `substrate update` — you'll be told what ran and what it did, not just "update complete."

## 0.1.6

**Workspace migration: engine path pinning**

This update includes an automatic migration that runs once on each workspace.
It pins the engine path in your workspace config so updates always use the
correct installed engine, regardless of any global environment variables on
your machine. No action required — it runs as part of `substrate update`.

## 0.1.5

**Housekeeping**

- `CLAUDE.md` now includes a header making clear it is engine-managed and will be overwritten on updates. To change how agents behave in your workspace, talk to your agent.
- Skills folder now includes a `README.md` explaining that skills are engine-provided and updated automatically with `substrate update`.

## 0.1.4

**Update detection improvements**

- Pending update notices now show version numbers (e.g., `0.1.2 → 0.1.4`) so you know exactly what you're being asked to install.
- Update notices now include these release notes, so you can decide whether to update before running `substrate update`.
- Running `substrate update` now refreshes the pending-updates check immediately — no waiting for the background service to confirm you're current.

**Engine isolation fix**

- Workspaces now pin their engine path at init time, preventing dev engine installs from leaking into other workspaces on the same machine.
- Engine resolution now respects workspace-level config over global environment variables.

## 0.1.3

Internal version bump. No user-facing changes.

## 0.1.2

Internal version bump. No user-facing changes.

## 0.1.1

Internal version bump. No user-facing changes.

## 0.1.0

Initial versioned release (R1). Establishes semantic versioning baseline.
