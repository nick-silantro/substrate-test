# Substrate Release Notes

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
