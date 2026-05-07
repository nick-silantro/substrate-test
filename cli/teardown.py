#!/usr/bin/env python3
"""Substrate teardown — stop services and remove engine + workspace.

Development/testing tool. Resets the machine to pre-install state so the
installer can be run again from scratch.

Usage:
    python3 teardown.py [options]

Options:
    --engine PATH    Engine path to remove  (default: ~/.substrate/engine)
    --instance PATH  Workspace path to remove (default: ~/substrate)
    --yes            Skip confirmation prompt
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE_DEFAULT   = Path.home() / ".substrate" / "engine"
INSTANCE_DEFAULT = Path.home() / "substrate"


def _bold(msg): print(f"\033[1m{msg}\033[0m")
def _ok(msg):   print(f"  \033[32m✓\033[0m {msg}")
def _warn(msg): print(f"  \033[33m!\033[0m {msg}")
def _info(msg): print(f"  {msg}")


# ---------------------------------------------------------------------------
# Service teardown — platform-specific
# ---------------------------------------------------------------------------

def _stop_services_mac(instance_path: Path) -> None:
    _bold("Stopping macOS services...")
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plists = list(launch_agents.glob("com.substrate.*.plist"))
    if not plists:
        _warn("No Substrate launchd services found")
    for plist in plists:
        subprocess.run(
            ["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)],
            capture_output=True,
        )
        plist.unlink(missing_ok=True)
        _ok(f"Unloaded and removed {plist.name}")
    # Kill any stragglers
    for pattern in ["entity-watcher.py", "evaluate-triggers.py"]:
        subprocess.run(["pkill", "-f", pattern], capture_output=True)
    print()


def _stop_services_linux(instance_path: Path) -> None:
    _bold("Stopping Linux services...")
    unit_dir = Path.home() / ".config" / "systemd" / "user"
    units = list(unit_dir.glob("substrate-*.service")) if unit_dir.exists() else []
    if not units:
        _warn("No Substrate systemd units found")
    for unit in units:
        subprocess.run(["systemctl", "--user", "stop",    unit.stem], capture_output=True)
        subprocess.run(["systemctl", "--user", "disable", unit.stem], capture_output=True)
        unit.unlink(missing_ok=True)
        _ok(f"Removed {unit.name}")
    if units:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
    for pattern in ["entity-watcher.py", "evaluate-triggers.py"]:
        subprocess.run(["pkill", "-f", pattern], capture_output=True)
    print()


def _stop_services_windows(instance_path: Path) -> None:
    _bold("Stopping Windows services...")

    # Stop and delete all tasks under \Substrate\ via PowerShell
    ps = (
        "Get-ScheduledTask -TaskPath '\\Substrate\\' -ErrorAction SilentlyContinue"
        " | Stop-ScheduledTask -ErrorAction SilentlyContinue;"
        " Get-ScheduledTask -TaskPath '\\Substrate\\' -ErrorAction SilentlyContinue"
        " | Unregister-ScheduledTask -Confirm:$false -ErrorAction SilentlyContinue"
    )
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        _ok("Task Scheduler tasks stopped and removed")
    else:
        _warn(f"Task Scheduler cleanup: {r.stderr.strip() or 'no tasks found'}")

    # Kill any wscript.exe launchers running our VBS wrappers
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-Process wscript -ErrorAction SilentlyContinue | Stop-Process -Force"],
        capture_output=True,
    )

    # Kill Python processes running entity-watcher or evaluate-triggers
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-WmiObject Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\""
         " | Where-Object { $_.CommandLine -match 'entity-watcher|evaluate-triggers' }"
         " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        capture_output=True,
    )
    _ok("Background processes stopped")

    # Remove registry Run key entries
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            to_delete = []
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(key, i)
                    if "substrate" in name.lower():
                        to_delete.append(name)
                    i += 1
                except OSError:
                    break
            for name in to_delete:
                winreg.DeleteValue(key, name)
            if to_delete:
                _ok(f"Registry Run key entries removed ({len(to_delete)})")
    except Exception as e:
        _warn(f"Registry cleanup skipped: {e}")

    # Remove CLI wrapper
    for candidate in [
        Path.home() / ".local" / "bin" / "substrate.bat",
        Path.home() / ".local" / "bin" / "substrate.cmd",
    ]:
        if candidate.exists():
            candidate.unlink()
            _ok(f"Removed {candidate}")

    print()


# ---------------------------------------------------------------------------
# Directory + CLI removal
# ---------------------------------------------------------------------------

def _remove_dir(path: Path, label: str) -> None:
    if path.exists():
        shutil.rmtree(path)
        _ok(f"Removed {label}: {path}")
    else:
        _info(f"{label} not found at {path} — skipping")


def _remove_cli_unix() -> None:
    for candidate in [
        Path.home() / ".local" / "bin" / "substrate",
        Path.home() / "bin" / "substrate",
        Path("/usr/local/bin/substrate"),
    ]:
        if candidate.exists() or candidate.is_symlink():
            candidate.unlink()
            _ok(f"Removed CLI symlink: {candidate}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(
        description="Substrate teardown (dev/testing)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--engine",   type=Path, default=ENGINE_DEFAULT,
                   help=f"Engine path to remove (default: {ENGINE_DEFAULT})")
    p.add_argument("--instance", type=Path, default=INSTANCE_DEFAULT,
                   help=f"Workspace path to remove (default: {INSTANCE_DEFAULT})")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    args = p.parse_args()

    engine_path   = args.engine.expanduser().resolve()
    instance_path = args.instance.expanduser().resolve()

    print()
    _bold("Substrate Teardown")
    print()
    _info(f"Engine:    {engine_path}")
    _info(f"Workspace: {instance_path}")
    _info("(Model cache at ~/.substrate/model-cache/ will be kept.)")
    print()

    if not args.yes:
        try:
            answer = input("  Stop all services and delete both directories? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)
        if answer.strip().lower() not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)
    print()

    if sys.platform == "darwin":
        _stop_services_mac(instance_path)
    elif sys.platform == "win32":
        _stop_services_windows(instance_path)
    else:
        _stop_services_linux(instance_path)

    _bold("Removing directories...")
    _remove_dir(instance_path, "Workspace")
    _remove_dir(engine_path,   "Engine")
    if sys.platform != "win32":
        _remove_cli_unix()
    print()

    _bold("Done. Ready for a fresh install.")
    print()


if __name__ == "__main__":
    main()
