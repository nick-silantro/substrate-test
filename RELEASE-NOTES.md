# Substrate Release Notes

Substrate is a knowledge graph that lives in your files. Most agent frameworks bolt memory on after the fact — wikis, vector stores, bags of embedded documents — Substrate starts from the data instead: typed, atomic entities in plain YAML that agents can read, write, query, and relate. Because the building blocks are information units rather than tool calls, the same graph that stores knowledge becomes a surface for action. Agents don't just use Substrate; they help build it.

---

## 0.1.14

**Foundations**

The first public release. Ships the full foundation layer: schema, entity operations, search, background services, and a bundled skill library.

**Knowledge graph**
- Entities are folders containing typed metadata in plain YAML — every person, project, idea, decision, and piece of work gets a structured home.
- Bidirectional relationships enforced automatically. Link two entities and both sides update.
- Schema defined in YAML: types, attributes, relationships, and groupings are data, not code.
- SQLite index is always rebuildable from files. Files are the source of truth; the database is a queryable view.

**Installation**
- Cross-platform: macOS, Windows, and Linux.
- `install.py` handles Python venv setup, Node.js and Claude CLI installation, and workspace init.
- Guided install via Claude: a `guided-start` conversation walks you through everything in plain language — no terminal commands required.

**CLI**
- `substrate init` — sets up a workspace, installs background services, and runs onboarding.
- `substrate update` — pulls the latest engine, runs pending migrations, updates services.
- `substrate workspaces` / `substrate remove` — list and remove workspaces.
- `substrate query` — search and filter entities from the command line.
- `substrate validate` — checks system integrity: entity structure, relationship consistency, schema compliance.

**Background services**
- `entity-watcher` keeps the SQLite index current as files change.
- `check-for-updates` runs hourly and writes a pending-updates notice to the workspace when a new version is available.
- Services use launchd (macOS), systemd (Linux), or Task Scheduler (Windows). Each workspace gets its own labeled service instance.

**Search**
- Hybrid search: FTS5 full-text and semantic vector search, blended with Reciprocal Rank Fusion.
- Fully local. The BAAI/bge-small-en-v1.5 model runs on-device via ONNX runtime — no API costs, no external dependencies. Cached at `~/.substrate/model-cache/` after first download (~25MB).

**Skills**
Eight skills bundled with the engine and available in every workspace:
`entity-management`, `entity-query`, `relationship-management`, `staging-intake`, `archive-management`, `system-validation`, `schema-evolution`, `context-sweep`.

**Automatic updates**
When a new version is available, a notice appears in your workspace context with the version delta and release notes. Migrations run automatically on update and each runs exactly once per workspace.

**License**
MIT.

---

## 0.1.14-5

**Windows installer fixes**

- After installing Node.js via winget, the installer now re-reads the system PATH from the Windows registry immediately — no restart required to find npm.
- Removed false "Claude Code not found" warning that appeared when the Claude CLI had just been installed but wasn't yet visible on the current session's PATH.
- Replaced Windows-specific "Close Claude Code completely" message with a cross-platform instruction to open a new terminal.
- Telemetry is now suppressed when `SUBSTRATE_NO_TELEMETRY=1` or `CI=true` is set — CI runners no longer count as installs.

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
