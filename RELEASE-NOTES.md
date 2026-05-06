# Substrate Release Notes

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
