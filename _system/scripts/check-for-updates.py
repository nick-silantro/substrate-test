#!/usr/bin/env python3
"""
Check for available updates to Substrate and the Anthropic stack.

Runs on a timer via background services. Every run is logged to
_system/logs/update-check-history.log regardless of findings — this log is the
primary testing artifact to confirm the service is firing.

If updates are available, writes _system/pending-updates.md for agents to
surface at session start. If everything is up to date, clears the file.

Usage:
  python3 check-for-updates.py
"""

import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

SUBSTRATE_PATH = os.environ.get(
    "SUBSTRATE_PATH",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

LOG_FILE = Path(SUBSTRATE_PATH) / "_system" / "logs" / "update-check-history.log"
PENDING_FILE = Path(SUBSTRATE_PATH) / "_system" / "docs" / "pending-updates.md"
SNOOZE_FILE = Path(SUBSTRATE_PATH) / "_system" / "update-snooze.yaml"

_METRICS_ENDPOINT = "https://substrate-metrics.substrate-registry.workers.dev/events"


def _fire_heartbeat() -> None:
    """Fire a heartbeat event at most once per 24 hours. Silent failure."""
    if os.environ.get("SUBSTRATE_NO_TELEMETRY") or os.environ.get("CI"):
        return
    try:
        import yaml
        import uuid
        import urllib.request
        import json
        from datetime import datetime, timezone

        config_path = Path("~/.substrate/config.yaml").expanduser()
        data = {}
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

        machine_id = data.get("machine_id")
        if not machine_id:
            return  # machine was never tracked (pre-metrics install), skip

        last_str = data.get("last_heartbeat")
        if last_str:
            last_dt = datetime.fromisoformat(last_str)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - last_dt).total_seconds() < 86400:
                return  # pinged within last 24 hours

        ep = _engine_path()
        version_file = ep / "VERSION"
        version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else None
        import sys as _sys
        platform = {"darwin": "darwin", "linux": "linux", "win32": "win32"}.get(
            _sys.platform, _sys.platform
        )

        payload = json.dumps({
            "event_type": "heartbeat",
            "machine_id": machine_id,
            "version": version,
            "platform": platform,
        }).encode("utf-8")
        req = urllib.request.Request(
            _METRICS_ENDPOINT,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "substrate"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=3)

        data["last_heartbeat"] = datetime.now(timezone.utc).isoformat()
        config_path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False), encoding="utf-8")
    except Exception:
        pass


def _engine_path():
    """Resolve the engine installation path.

    Priority: overlay.yaml (workspace-specific) > SUBSTRATE_ENGINE_PATH (global) > default.
    """
    overlay = Path(SUBSTRATE_PATH) / "_system" / "overlay.yaml"
    if overlay.exists():
        import yaml
        with open(overlay, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        ep = data.get("engine")
        if ep:
            return Path(ep).expanduser()
    env = os.environ.get("SUBSTRATE_ENGINE_PATH")
    if env:
        return Path(env).expanduser()
    return Path("~/.substrate/engine").expanduser()


def _update_channel():
    """Read the configured update channel (main or dev)."""
    overlay = Path(SUBSTRATE_PATH) / "_system" / "overlay.yaml"
    if overlay.exists():
        import yaml
        with open(overlay, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data.get("update_channel", "main")
    return "main"


def _log(msg: str):
    now = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S %Z")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{now}] {msg}\n")


def _read_snooze() -> dict:
    """Return the current snooze record, or {} if none."""
    if not SNOOZE_FILE.exists():
        return {}
    try:
        import yaml
        with open(SNOOZE_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _venv_pip() -> Path | None:
    """Return path to pip in the workspace venv, or None if not found."""
    for suffix in ("bin/pip", "Scripts/pip.exe", "Scripts/pip"):
        p = Path(SUBSTRATE_PATH) / "_system" / "venv" / suffix
        if p.exists():
            return p
    return None


def _find_npm_tool(name: str) -> str | None:
    """Find an npm-installed CLI tool (claude, npm), handling Windows PATH gaps.

    On Windows, background services and Git Bash don't inherit the user's full
    PATH, so shutil.which often misses npm-global tools. We fall back to the
    canonical npm global bin directory (%APPDATA%\\npm) and, for npm itself,
    the Node.js Program Files install location.
    """
    import shutil
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidate = Path(appdata) / "npm" / f"{name}.cmd"
            if candidate.exists():
                return str(candidate)
        if name == "npm":
            for pf in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramW6432")]):
                candidate = Path(pf) / "nodejs" / "npm.cmd"
                if candidate.exists():
                    return str(candidate)
    return None


def _run_tool(tool_path: str, args: list) -> "subprocess.CompletedProcess":
    """Run a tool by full path, routing .cmd files through cmd.exe on Windows."""
    if sys.platform == "win32" and tool_path.endswith(".cmd"):
        return subprocess.run(["cmd.exe", "/c", tool_path, *args],
                              capture_output=True, text=True, timeout=30)
    return subprocess.run([tool_path, *args], capture_output=True, text=True, timeout=30)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _parse_version(s: str) -> tuple:
    """Parse a version string into a sortable tuple.

    Handles clean releases ('0.1.0') and pre-release builds ('0.1.0-2').
    Pre-release builds sort before the clean release of the same base version:
      0.1.0-0 < 0.1.0-1 < 0.1.0 < 0.1.1-0
    """
    try:
        s = s.strip()
        if "-" in s:
            base, pre = s.rsplit("-", 1)
            return tuple(int(x) for x in base.split(".")) + (int(pre),)
        return tuple(int(x) for x in s.split(".")) + (float("inf"),)
    except (ValueError, AttributeError):
        return (0, 0, 0, 0)


def _fetch_substrate_release_notes(ep: Path, channel: str, local_version: str | None, remote_version: str) -> str:
    """Fetch RELEASE-NOTES.md from the remote and extract sections newer than local_version."""
    try:
        result = subprocess.run(
            ["git", "show", f"origin/{channel}:RELEASE-NOTES.md"],
            cwd=ep, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return ""

        content = result.stdout
        local_v = _parse_version(local_version) if local_version else (0, 0, 0, 0)

        # Extract all version sections newer than local_version, up to and including remote_version.
        import re
        sections = re.split(r"(?=^## \d)", content, flags=re.MULTILINE)
        included = []
        for section in sections:
            m = re.match(r"^## (\d[\d.]+)", section)
            if not m:
                continue
            v = _parse_version(m.group(1))
            if v > local_v and v <= _parse_version(remote_version):
                included.append(section.strip().replace("## ", "### ", 1))

        return "\n\n".join(included)
    except Exception:
        return ""


def check_substrate() -> tuple[bool | None, str, str]:
    """Return (update_available, detail, release_notes). None means check failed."""
    try:
        ep = _engine_path()
        channel = _update_channel()
        if not ep.exists():
            return None, "engine path not found", ""

        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=ep, capture_output=True, text=True, timeout=30
        )

        # Version-aware comparison: read VERSION file from local and remote.
        local_version_file = ep / "VERSION"
        local_version = local_version_file.read_text(encoding="utf-8").strip() if local_version_file.exists() else None

        remote_result = subprocess.run(
            ["git", "show", f"origin/{channel}:VERSION"],
            cwd=ep, capture_output=True, text=True, timeout=10
        )
        remote_version = remote_result.stdout.strip() if remote_result.returncode == 0 else None

        if local_version and remote_version:
            if _parse_version(remote_version) > _parse_version(local_version):
                snooze = _read_snooze()
                if snooze.get("substrate") == remote_version:
                    return False, f"snoozed ({remote_version})", ""
                notes = _fetch_substrate_release_notes(ep, channel, local_version, remote_version)
                return True, f"{local_version} → {remote_version}", notes
            return False, f"up to date ({local_version})", ""

        if remote_version and not local_version:
            snooze = _read_snooze()
            if snooze.get("substrate") == remote_version:
                return False, f"snoozed ({remote_version})", ""
            notes = _fetch_substrate_release_notes(ep, channel, None, remote_version)
            return True, f"→ {remote_version}", notes

        # Fall back to commit count when VERSION file is absent on both sides.
        result = subprocess.run(
            ["git", "rev-list", f"HEAD..origin/{channel}", "--count"],
            cwd=ep, capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return None, f"git rev-list failed: {result.stderr.strip()}", ""

        count = int(result.stdout.strip())
        if count > 0:
            latest_hash = subprocess.run(
                ["git", "rev-parse", f"origin/{channel}"],
                cwd=ep, capture_output=True, text=True, timeout=10
            ).stdout.strip()
            snooze = _read_snooze()
            if latest_hash and snooze.get("substrate") == latest_hash:
                return False, f"snoozed ({latest_hash[:8]})", ""
            return True, f"{count} new commit(s) on origin/{channel}", ""
        return False, "up to date", ""
    except Exception as e:
        return None, str(e), ""


def check_agent_sdk() -> tuple[bool | None, str, str]:
    """Check if a newer @anthropic-ai/claude-agent-sdk is available on npm.
    Returns (update_available, detail, changelog_text).
    """
    try:
        ep = _engine_path()
        current = None
        for app_dir in ("web/surface", "web/relay"):
            pkg = ep / app_dir / "node_modules" / "@anthropic-ai" / "claude-agent-sdk" / "package.json"
            if pkg.exists():
                data = json.loads(pkg.read_text(encoding="utf-8"))
                current = data.get("version")
                break
        if not current:
            return False, "not installed", ""

        npm = _find_npm_tool("npm")
        if npm is None:
            return None, "npm not found", ""
        npm_result = _run_tool(npm, ["view", "@anthropic-ai/claude-agent-sdk", "version"])
        if npm_result.returncode != 0:
            return None, "npm check failed", ""
        latest = npm_result.stdout.strip()

        if current == latest:
            return False, f"up to date ({current})", ""

        snooze = _read_snooze()
        if snooze.get("agent_sdk") == latest:
            return False, f"snoozed ({latest})", ""

        changelog = _fetch_sdk_changelog(current, latest)
        return True, f"{current} → {latest}", changelog
    except Exception as e:
        return None, str(e), ""


def _fetch_sdk_changelog(current: str, latest: str) -> str:
    """Fetch Agent SDK changelog entries between current (exclusive) and latest (inclusive)."""
    try:
        url = "https://raw.githubusercontent.com/anthropics/claude-agent-sdk-typescript/main/CHANGELOG.md"
        with urllib.request.urlopen(url, timeout=15) as resp:
            content = resp.read().decode("utf-8")

        import re

        def parse_ver(v: str) -> tuple:
            try:
                return tuple(int(x) for x in v.strip().split(".")[:3])
            except Exception:
                return (0, 0, 0)

        current_v = parse_ver(current)
        latest_v = parse_ver(latest)

        sections = re.split(r"^## ", content, flags=re.MULTILINE)
        relevant = []
        for section in sections[1:]:
            lines = section.split("\n", 1)
            ver_str = lines[0].strip()
            ver = parse_ver(ver_str)
            if current_v < ver <= latest_v:
                body = lines[1].strip() if len(lines) > 1 else ""
                relevant.append(f"**{ver_str}**\n{body}")

        return "\n\n".join(relevant)
    except Exception:
        return ""


def check_claude_cli() -> tuple[bool | None, str]:
    """Check if a newer Claude Code CLI is available via npm."""
    try:
        claude = _find_npm_tool("claude")
        if claude is None:
            return False, "not in PATH — skipping"

        result = _run_tool(claude, ["--version"])
        raw = (result.stdout.strip() or result.stderr.strip()).split("\n")[0]
        parts = raw.replace("/", " ").split()
        current = next((p for p in parts if p and p[0].isdigit()), None)
        if not current:
            return False, "not in PATH — skipping"

        npm = _find_npm_tool("npm")
        if npm is None:
            return None, "npm not found"
        npm_result = _run_tool(npm, ["view", "@anthropic-ai/claude-code", "version"])
        if npm_result.returncode != 0:
            return None, "npm check failed"
        latest = npm_result.stdout.strip()

        if current == latest:
            return False, f"up to date ({current})"

        snooze = _read_snooze()
        if snooze.get("claude_cli") == latest:
            return False, f"snoozed ({latest})"

        return True, f"{current} → {latest}"
    except Exception as e:
        return None, str(e)


def check_pending_migrations() -> list[str]:
    """Return names of migration scripts not yet applied to this workspace."""
    try:
        ep = _engine_path()
        migrations_dir = ep / "_system" / "migrations"
        if not migrations_dir.exists():
            return []

        applied_log = Path(SUBSTRATE_PATH) / "_system" / "migrations-applied.json"
        applied: set[str] = set()
        if applied_log.exists():
            import json as _json
            data = _json.loads(applied_log.read_text(encoding="utf-8"))
            if isinstance(data, list):
                applied = set(data)

        pending = sorted(
            p.name for p in migrations_dir.glob("*.py")
            if p.stem[0].isdigit() and p.name not in applied
        )
        return pending
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _write_pending(
    substrate_detail: str | None,
    substrate_notes: str | None,
    pending_migrations: list[str],
    sdk_detail: str | None,
    sdk_changelog: str | None,
    cli_detail: str | None,
):
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    lines = [
        "---\n"
        "name: Pending Updates\n"
        "startup_for: [all]\n"
        "context_audience: [all]\n"
        "---\n\n"
        f"# Pending Updates\n\n_Last checked: {now}_\n\n"
    ]

    if substrate_detail:
        lines.append(
            f"## Substrate Engine\n\n"
            f"New version available: {substrate_detail}\n\n"
            "```\nsubstrate update\n```\n\n"
        )
        if pending_migrations:
            count = len(pending_migrations)
            noun = "migration" if count == 1 else "migrations"
            lines.append(
                f"**Note:** This update includes {count} workspace {noun} that will run automatically.\n"
                f"Ask the agent what changed before approving if you'd like details.\n\n"
            )
        if substrate_notes:
            lines.append(f"{substrate_notes}\n\n")

    if sdk_detail or cli_detail:
        lines.append("## Anthropic Stack\n\n")
        if cli_detail:
            lines.append(f"- Claude Code CLI: {cli_detail}\n")
        if sdk_detail:
            lines.append(f"- Agent SDK (`@anthropic-ai/claude-agent-sdk`): {sdk_detail}\n")
        lines.append("\n```\nsubstrate update-anthropic\n```\n\n")
        if sdk_changelog:
            lines.append(f"### What's new in the Agent SDK\n\n{sdk_changelog}\n\n")

    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text("".join(lines), encoding="utf-8")


def main():
    _log("check started")

    substrate_available, substrate_detail, substrate_notes = check_substrate()
    _log(f"  substrate: {substrate_detail}")

    sdk_available, sdk_detail, sdk_changelog = check_agent_sdk()
    _log(f"  agent-sdk: {sdk_detail}")

    cli_available, cli_detail = check_claude_cli()
    _log(f"  claude-cli: {cli_detail}")

    has_updates = substrate_available or sdk_available or cli_available
    any_failed = substrate_available is None or sdk_available is None or cli_available is None

    pending_migrations = check_pending_migrations() if substrate_available else []
    if pending_migrations:
        _log(f"  migrations pending: {', '.join(pending_migrations)}")

    if has_updates:
        _write_pending(
            substrate_detail=substrate_detail if substrate_available else None,
            substrate_notes=substrate_notes if substrate_available else None,
            pending_migrations=pending_migrations,
            sdk_detail=sdk_detail if sdk_available else None,
            sdk_changelog=sdk_changelog if sdk_available else None,
            cli_detail=cli_detail if cli_available else None,
        )
        _log("pending-updates.md written")
    elif any_failed:
        # One or more checks failed — preserve existing update content but refresh
        # the timestamp so "Last checked" stays current.
        if PENDING_FILE.exists():
            import re as _re
            now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
            content = PENDING_FILE.read_text(encoding="utf-8")
            content = _re.sub(r"_Last checked: [^_]*_", f"_Last checked: {now}_", content)
            PENDING_FILE.write_text(content, encoding="utf-8")
        _log("some checks failed — preserved pending-updates.md, refreshed timestamp")
    else:
        if PENDING_FILE.exists():
            PENDING_FILE.unlink()
            _log("all up to date — cleared pending-updates.md")
        else:
            _log("all up to date")

    _fire_heartbeat()
    _log("check done")


if __name__ == "__main__":
    main()
