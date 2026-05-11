# Background Services

These two services run in the background automatically. You rarely interact with them directly — if something seems off, ask your agent to validate the workspace first (`validate my workspace`). If the agent flags a specific service issue, or you want to restart one manually, this page covers the details.

---

## Entity Watcher

**What it does:** Monitors your workspace folder for file changes and keeps the database synchronized. When any file in your workspace changes — whether made by your agent or by you directly — Entity Watcher detects it and updates the index.

**When it runs:** Continuously, in the background, from the moment you log in.

**Platform service names** (replace `substrate` with your workspace folder name if you used a different location):
- Mac: `com.substrate.entity-watcher.substrate`
- Linux: `substrate-entity-watcher-substrate`
- Windows: `Substrate\EntityWatcher-substrate`

### If Entity Watcher won't start

The most common cause is a missing Python virtual environment. Fix it first, then restart the service.

**Mac and Linux — set up the environment:**
```
python3 -m venv ~/substrate/_system/venv && ~/substrate/_system/venv/bin/pip install watchdog
```

**Mac — restart the service:**
```
launchctl bootout gui/$(id -u)/com.substrate.entity-watcher.substrate && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.substrate.entity-watcher.substrate.plist
```

**Linux — restart the service:**
```
systemctl --user restart substrate-entity-watcher-substrate
```

**Windows (PowerShell) — set up the environment and restart:**
```
python -m venv $env:USERPROFILE\substrate\_system\venv
$env:USERPROFILE\substrate\_system\venv\Scripts\pip install watchdog
schtasks /Run /TN "Substrate\EntityWatcher-substrate"
```

---

## Evaluate Triggers

**What it does:** Runs periodic background processing — handling automated workspace events such as recurring tasks coming due and dependent tasks unlocking when prerequisites complete.

**When it runs:** Periodically in the background.

**Platform service names** (replace `substrate` with your workspace folder name if you used a different location):
- Mac: `com.substrate.evaluate-triggers.substrate` (launchd timer)
- Linux: `substrate-evaluate-triggers-substrate` (systemd timer)
- Windows: `Substrate\EvaluateTriggers-substrate`

If this service stops running, your workspace still functions for reading and writing — only automated periodic events are affected until it's restarted.

### If Evaluate Triggers won't start

**Mac — restart the service:**
```
launchctl bootout gui/$(id -u)/com.substrate.evaluate-triggers.substrate && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.substrate.evaluate-triggers.substrate.plist
```

**Linux — restart the service:**
```
systemctl --user restart substrate-evaluate-triggers-substrate.timer
```

**Windows (PowerShell):**
```
schtasks /Run /TN "Substrate\EvaluateTriggers-substrate"
```

---

## Checking service status

To verify both services are running:

**Mac:**
```
launchctl list | grep substrate
```
Running services appear with a PID in the first column. A `-` means the service is not currently running. You'll also see the full service names here, which is useful if your workspace has a non-default name.

**Linux:**
```
systemctl --user status substrate-entity-watcher
systemctl --user status substrate-evaluate-triggers.timer
```

**Windows:** Open Task Manager → Task Scheduler Library → Substrate. Both tasks should show a status of **Ready** or **Running**.

---

## Removing a workspace

If you delete a workspace folder without unregistering it first, the background services keep running. Use `substrate workspaces` to see what's registered, and `substrate remove PATH` to unregister the services — this works even after the directory is gone.

To remove both the services and the folder in one step:
```
substrate remove ~/substrate --delete
```

---

- [Updates and Services](../learn/updates-and-services.md) — how updates work and what the services do at a high level
- [CLI Reference](cli.md) — the `substrate validate` command for workspace health checks
- [How-To Guides](../learn/how-to-guides.md) — common tasks in recipe form
