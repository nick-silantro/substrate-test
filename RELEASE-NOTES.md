# Substrate Release Notes

## 0.1.13-3

**Release notes and service update discipline**

- `substrate update` now reinstalls background services automatically, so configuration changes (like updated check intervals) take effect without running `substrate init --reinit` separately.
- Added release notes discipline to operator documentation — every push to substrate-test or substrate-core now requires a RELEASE-NOTES.md entry.

## 0.1.13-2

**Snooze now clears the pending update notice**

- Fixed: snoozed updates were leaving the pending-updates notification in place, causing it to resurface in the next session even though the version had been declined. The notice is now cleared immediately on snooze.

## 0.1.13-1

**Windows update fix: skills folder**

- Fixed an error during `substrate update` on Windows where the skills folder (a directory junction) couldn't be removed. The update now handles Windows directory junctions correctly.

## 0.1.13-0

**Windows compatibility**

- Fixed installation hang: the Windows PATH broadcast no longer blocks on unresponsive windows.
- Fixed encoding crashes: all file reads and writes now explicitly use UTF-8. Previously, Windows defaulted to cp1252, which corrupted entity files containing em-dashes, arrows, or other non-ASCII characters and caused session startup to crash.
- Added a bash shim (`substrate`) alongside `substrate.bat` so the CLI works correctly in Git Bash (the shell Claude Code uses on Windows).
- Set `PYTHONUTF8=1` in the Windows shim so all child processes inherit UTF-8 encoding.

## 0.1.12

**Install metrics**

- Substrate now tracks installs and active users to help measure how the project is growing. A machine ID is generated once per machine; a workspace ID is generated per workspace. Events fired: machine_install (first workspace on a machine), workspace_init (every substrate init), heartbeat (daily, from the background update check), and update (on successful substrate update). No PII collected.

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
