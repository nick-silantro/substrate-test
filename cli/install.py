#!/usr/bin/env python3
"""Substrate cross-platform installer.

Usage:
    python3 install.py [options]

Options:
    --engine PATH       Where to install the engine  (default: ~/.substrate/engine)
    --instance PATH     Where to create the workspace (default: ~/substrate)
    --source public|private
    --repo URL          Override engine repo URL directly
    --tag vX.Y.Z        Pin to a specific release tag

Examples:
    python3 install.py
    python3 install.py --instance ~/my-workspace
    python3 install.py --repo https://github.com/nick-silantro/substrate-test.git
"""

import argparse
import os
import sys
import subprocess
import shutil
from pathlib import Path

ENGINE_DEFAULT  = Path.home() / ".substrate" / "engine"
INSTANCE_DEFAULT = Path.home() / "substrate"
REPO_PUBLIC  = "https://github.com/nick-silantro/substrate-core.git"
REPO_PRIVATE = "https://github.com/nick-silantro/substrate-engine.git"
MIN_PYTHON   = (3, 9)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _bold(msg: str) -> None: print(f"\033[1m{msg}\033[0m")
def _ok(msg: str)   -> None: print(f"  \033[32m✓\033[0m {msg}")
def _info(msg: str) -> None: print(f"  {msg}")
def _warn(msg: str) -> None: print(f"  \033[33m!\033[0m {msg}")
def _die(msg: str)  -> None: print(f"  \033[31m✗\033[0m {msg}", file=sys.stderr); sys.exit(1)


# ---------------------------------------------------------------------------
# Claude Code detection
# ---------------------------------------------------------------------------

def _is_inside_claude_code() -> bool:
    """Walk the process tree looking for a Claude Code parent process."""
    try:
        if sys.platform == "win32":
            # Single PowerShell call to get all processes — avoids per-process
            # startup overhead of iterative calls.
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-Process | ForEach-Object {"
                 "  $ppid = if ($_.Parent) { $_.Parent.Id } else { 0 };"
                 "  \"$($_.Id)|$ppid|$($_.Name)\""
                 "}"],
                capture_output=True, text=True, timeout=10,
            )
            procs = {}
            for line in r.stdout.splitlines():
                parts = line.strip().split("|", 2)
                if len(parts) == 3:
                    pid_s, ppid_s, name = parts
                    if pid_s.isdigit() and ppid_s.isdigit():
                        procs[int(pid_s)] = (int(ppid_s), name)
            pid = os.getpid()
            for _ in range(15):
                if pid not in procs:
                    break
                ppid, name = procs[pid]
                if "claude" in name.lower():
                    return True
                if ppid in (0, 1, pid):
                    break
                pid = ppid
        else:
            # Unix: one ps call per level — fast since ps is a thin syscall.
            pid = os.getpid()
            for _ in range(15):
                r = subprocess.run(
                    ["ps", "-p", str(pid), "-o", "ppid=,comm="],
                    capture_output=True, text=True, timeout=3,
                )
                parts = r.stdout.strip().split(None, 1)
                if len(parts) < 2:
                    break
                ppid_str, name = parts
                if "claude" in name.lower():
                    return True
                ppid = int(ppid_str.strip()) if ppid_str.strip().lstrip("-").isdigit() else 0
                if ppid in (0, 1, pid):
                    break
                pid = ppid
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Substrate installer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--engine",   type=Path, default=ENGINE_DEFAULT,
                   help="Engine install path (default: ~/.substrate/engine)")
    p.add_argument("--instance", type=Path, default=INSTANCE_DEFAULT,
                   help="Workspace path (default: ~/substrate)")
    p.add_argument("--source",   choices=["public", "private"], default="public")
    p.add_argument("--repo",     help="Override engine repo URL")
    p.add_argument("--tag",      help="Pin to a release tag, e.g. v0.1.0")
    args = p.parse_args()
    if args.repo is None:
        args.repo = REPO_PUBLIC if args.source == "public" else REPO_PRIVATE
    return args


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def _check_prerequisites() -> None:
    _bold("Checking prerequisites...")

    # Python — we're already running, just check the version
    if sys.version_info < MIN_PYTHON:
        ver = ".".join(str(x) for x in MIN_PYTHON)
        _die(f"Python {ver}+ required (found {sys.version.split()[0]})")
    _ok(f"Python {sys.version.split()[0]}")

    # PyYAML — install silently if missing
    try:
        import yaml  # noqa: F401
        _ok("PyYAML")
    except ImportError:
        _info("Installing PyYAML...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyyaml", "--quiet"],
            capture_output=True,
        )
        if r.returncode != 0:
            _die("Failed to install PyYAML. Run: pip install pyyaml")
        _ok("PyYAML installed")

    # Git
    r = subprocess.run(["git", "--version"], capture_output=True, text=True)
    if r.returncode != 0:
        _die("Git is required. Install from https://git-scm.com")
    _ok(f"Git {r.stdout.strip().split()[-1]}")

    # Claude Code — required, but only warn so the installer doesn't block
    if shutil.which("claude") or _is_inside_claude_code():
        _ok("Claude Code")
    else:
        _warn("Claude Code not found on PATH and not detected as a running process.")
        _warn("If not yet installed: https://claude.ai/code")

    print()


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _install_engine(engine_path: Path, repo_url: str, tag: str | None) -> Path:
    _bold(f"Installing engine to {engine_path}...")

    if (engine_path / ".git").exists():
        _info("Engine already installed — pulling latest...")
        r = subprocess.run(
            ["git", "-C", str(engine_path), "pull", "--ff-only", "origin", "main"],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            _die(f"Failed to update engine: {r.stderr.strip()}")
        _ok("Engine updated")
    elif engine_path.exists() and any(engine_path.iterdir()):
        _die(
            f"{engine_path} exists but is not a Substrate engine. "
            "Use --engine to specify a different path."
        )
    else:
        engine_path.parent.mkdir(parents=True, exist_ok=True)
        _info("Cloning engine...")
        clone_cmd = ["git", "clone", "--depth", "1"]
        if tag:
            clone_cmd += ["--branch", tag]
        clone_cmd += [repo_url, str(engine_path)]
        r = subprocess.run(clone_cmd, capture_output=True, text=True)
        if r.returncode != 0:
            _die(f"Failed to clone engine. Check your network connection.\n{r.stderr.strip()}")
        _ok(f"Engine installed{f' @ {tag}' if tag else ''}")

    cli_src = engine_path / "cli" / "substrate"
    if not cli_src.exists():
        _die(f"CLI not found at {cli_src} — engine install may be incomplete.")
    if sys.platform != "win32":
        cli_src.chmod(cli_src.stat().st_mode | 0o111)

    print()
    return cli_src


# ---------------------------------------------------------------------------
# CLI setup
# ---------------------------------------------------------------------------

def _install_cli(cli_src: Path) -> None:
    _bold("Installing substrate CLI...")
    if sys.platform == "win32":
        _install_cli_windows(cli_src)
    else:
        _install_cli_unix(cli_src)
    print()


def _install_cli_unix(cli_src: Path) -> None:
    path_dirs = os.environ.get("PATH", "").split(":")
    candidates = [Path.home() / ".local" / "bin", Path.home() / "bin", Path("/usr/local/bin")]

    for d in candidates:
        if str(d) in path_dirs and d.exists() and os.access(d, os.W_OK):
            _symlink(cli_src, d / "substrate")
            _ok(f"CLI linked to {d}/substrate")
            return

    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    _symlink(cli_src, local_bin / "substrate")
    _ok(f"CLI linked to {local_bin}/substrate")

    profile = _detect_shell_profile()
    if profile and not _contains(profile, ".local/bin"):
        with open(profile, "a") as f:
            f.write('\n# Substrate CLI\nexport PATH="$HOME/.local/bin:$PATH"\n')
        _info(f"Added ~/.local/bin to PATH in {profile}")

    _warn(f"Restart your terminal (or run: source {profile or '~/.zshrc'}) before using the substrate command")


def _install_cli_windows(cli_src: Path) -> None:
    local_bin = Path.home() / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)

    # .bat wrapper so `substrate` works in cmd and PowerShell without .py extension
    bat = local_bin / "substrate.bat"
    bat.write_text(f'@echo off\n"{sys.executable}" "{cli_src}" %*\n', encoding="utf-8")
    _ok(f"CLI wrapper created at {bat}")

    # Add to user PATH and set env vars via registry
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        ) as key:
            try:
                cur = winreg.QueryValueEx(key, "PATH")[0]
            except FileNotFoundError:
                cur = ""
            s = str(local_bin)
            if s not in cur:
                winreg.SetValueEx(key, "PATH", 0, winreg.REG_EXPAND_SZ,
                                  f"{s};{cur}" if cur else s)
                _info("Added ~/.local/bin to user PATH")
        # Broadcast WM_SETTINGCHANGE so Explorer and running apps reload the
        # environment from the registry immediately — without this, apps
        # launched from the taskbar/Start menu won't see the new PATH until
        # the user logs out and back in.
        try:
            import ctypes
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            ctypes.windll.user32.SendMessageW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment")
        except Exception:
            pass  # Non-fatal — PATH is in the registry, will apply on next login
        _ok("User PATH updated")
    except Exception as e:
        _warn(f"Could not update PATH automatically: {e}")
        _warn(f"Add to your PATH manually: {local_bin}")


# ---------------------------------------------------------------------------
# Workspace + env vars
# ---------------------------------------------------------------------------

def _setup_workspace(cli_src: Path, engine_path: Path, instance_path: Path) -> None:
    _bold(f"Setting up workspace at {instance_path}...")

    if (instance_path / "CLAUDE.md").exists():
        _ok("Workspace already exists — skipping creation")
        print()
        return

    env = {
        **os.environ,
        "SUBSTRATE_ENGINE_PATH": str(engine_path),
        "SUBSTRATE_PATH": str(instance_path),
    }
    r = subprocess.run(
        [sys.executable, str(cli_src), "init", str(instance_path)],
        env=env,
    )
    if r.returncode != 0:
        _die("Failed to create workspace. See above for details.")

    _write_env_vars(engine_path, instance_path)
    print()


def _write_env_vars(engine_path: Path, instance_path: Path) -> None:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_WRITE
            ) as key:
                winreg.SetValueEx(key, "SUBSTRATE_ENGINE_PATH", 0,
                                  winreg.REG_SZ, str(engine_path))
                winreg.SetValueEx(key, "SUBSTRATE_PATH", 0,
                                  winreg.REG_SZ, str(instance_path))
            _info("Substrate env vars written to user environment")
        except Exception as e:
            _warn(f"Could not set env vars automatically: {e}")
            _warn(f"Set these manually: SUBSTRATE_ENGINE_PATH={engine_path}  SUBSTRATE_PATH={instance_path}")
    else:
        profile = _detect_shell_profile()
        if profile and not _contains(profile, "SUBSTRATE_ENGINE_PATH"):
            with open(profile, "a") as f:
                f.write(
                    f"\n# Substrate\n"
                    f'export SUBSTRATE_ENGINE_PATH="{engine_path}"\n'
                    f'export SUBSTRATE_PATH="{instance_path}"\n'
                )
            _info(f"Added Substrate env vars to {profile}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _symlink(src: Path, dest: Path) -> None:
    if dest.is_symlink() or dest.exists():
        dest.unlink()
    dest.symlink_to(src)


def _detect_shell_profile() -> Path | None:
    home = Path.home()
    for name in (".zshrc", ".bashrc", ".profile"):
        p = home / name
        if p.exists():
            return p
    return None


def _contains(path: Path, text: str) -> bool:
    try:
        return text in path.read_text()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        os.environ["PYTHONUTF8"] = "1"  # propagate UTF-8 to all child processes
    args = _parse_args()
    _check_prerequisites()
    cli_src = _install_engine(args.engine, args.repo, args.tag)
    _install_cli(cli_src)
    _setup_workspace(cli_src, args.engine, args.instance)

    _bold("Substrate is ready.")
    print()
    _info(f"Workspace:  {args.instance}")
    _info(f"Engine:     {args.engine}")
    print()
    if sys.platform == "win32":
        _info("Close Claude Code completely and reopen it — the substrate command will be on your PATH.")
        print()
    _info("Open your workspace in Claude Code to get started:")
    print()
    if sys.platform == "win32":
        print(f"    cd {args.instance}")
        print( "    claude")
    else:
        print(f"    cd {args.instance} && claude")
    print()
    _info("Claude will load your orientation automatically from CLAUDE.md.")


if __name__ == "__main__":
    main()
