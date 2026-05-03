# Substrate Deployment Units

Service configs for Substrate's background infrastructure. These get installed
into the OS's service manager (launchd on macOS, systemd on Linux).

## Placeholder convention

Templates use `__PLACEHOLDER__` tokens that must be substituted before
installing. Each file's header comment lists its placeholders and provides a
`sed` one-liner to apply them.

| Placeholder | Meaning | Typical value |
|---|---|---|
| `__WORKSPACE__` | Workspace root | `~/substrate` or any path |
| `__ENGINE__` | Engine install | `~/.substrate/engine` (release) or `~/dev/substrate-engine` (dev) |
| `__HOME__` | User home directory | `$HOME` |
| `__USER__` | OS username | `$USER` |
| `__SUBSTRATE__` | `substrate` CLI binary | `~/.local/bin/substrate` |

The workspace and engine paths are independent — the workspace is where
entities live; the engine is where scripts and schema live. Scripts are never
copied into the workspace; services call them through the engine path.

## Services

### entity-watcher — filesystem change detector

Watches `entities/**/` for modifications to content files and bumps the owning
entity's `last_edited` timestamp. Harness-agnostic — fires for any file change
regardless of who wrote it.

- `com.substrate.entity-watcher.plist` — launchd (macOS)
- `substrate-entity-watcher.service` — systemd (Linux)

**Dependency:** the `watchdog` Python package must be installed in
`__WORKSPACE__/_system/venv/`. See venv notes below.

### evaluate-triggers — recurrence and agent trigger evaluator

Runs every 5 minutes to promote ready chores and spawn due agent triggers.
Calls `substrate triggers evaluate` through the CLI.

- `com.substrate.evaluate-triggers.plist` — launchd (macOS)

## Future: generator

The right long-term solution is `substrate init` generating these files
automatically from workspace and engine paths. Until then, use the `sed`
one-liners in each file's header comment.

## Venv setup

`_system/venv/` contains Python binaries compiled for the host architecture.
**Do not copy a venv between machines of different architectures** — this has
broken things before (Mac ARM → Linux x86 silently fails).

On a new install or after migrating the workspace:

    rm -rf _system/venv
    python3 -m venv _system/venv
    _system/venv/bin/pip install -r requirements.txt

When rsyncing the workspace, always exclude the venv:

    rsync -avz --exclude '_system/venv/' --exclude 'node_modules/' ...
