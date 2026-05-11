#!/usr/bin/env python3
"""Substrate entity file watcher.

Watches `entities/**/` for modifications to content files (everything EXCEPT
meta.yaml and sidecars like `meta.yaml.lock`, `.tmp_*`) and bumps the owning
entity's `last_edited` timestamp in both meta.yaml and the SQLite index. Also
appends a changelog entry.

This replaces the PostToolUse Claude Code hook (`.claude/hooks/post-file-write.py`)
with a harness-agnostic mechanism:
  - The hook only fires under Claude Code interactive / SDK agent sessions.
  - The watcher fires for any file change — scripts, humans editing via VSCode,
    agents in other harnesses, rsync pushes, git checkouts, anything.

Design:
  - watchdog (inotify on Linux, FSEvents on macOS, ReadDirectoryChangesW on Windows) for efficient event delivery.
  - Ignore list prevents infinite loops: we WRITE meta.yaml in response to content
    changes, so watching meta.yaml would re-trigger us.
  - Per-entity debounce: multiple rapid changes to the same entity within
    DEBOUNCE_SECONDS coalesce into a single timestamp bump.
  - Canonical timestamp emission via lib.fileio.quote_yaml_scalar — the whole
    point of this path is to keep meta.yaml canonical regardless of what writes
    entity content.

Run as a launchd service (macOS) or systemd user unit (Linux). Logs to stderr
which the service manager captures.
"""

import os
import re
import sys
import time
import signal
import sqlite3
import logging
import threading
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from lib.fileio import quote_yaml_scalar, safe_write
from changelog import log_change


# Path pattern: entities/{type}/{2hex}/{2hex}/{uuid}/{filename}
ENTITY_PATH_RE = re.compile(
    r"entities/([^/]+)/([0-9a-f]{2})/([0-9a-f]{2})/"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(.+)$"
)

# Filenames and patterns we must NOT react to:
# - `meta.yaml`: we write it ourselves; reacting would loop infinitely.
# - `meta.yaml.lock`: fcntl lock sidecar from lib/fileio.safe_write.
# - `.tmp_*`: atomic-write staging files.
# - Anything starting with `.` (hidden / editor swap files like `.swp`).
IGNORED_BASENAMES = {"meta.yaml", "meta.yaml.lock"}
IGNORED_PREFIXES = (".tmp_", ".")

# Debounce window per entity. Multiple changes to the same entity within this
# window collapse to a single last_edited bump. Prevents thrashing from editors
# that save frequently (VSCode's "auto-save after delay = 1000ms") and from
# scripts that rewrite several content files in quick succession.
DEBOUNCE_SECONDS = 1.5

# Poll interval for the debounce-flush thread.
FLUSH_INTERVAL_SECONDS = 0.5


def _pid_is_running(pid: int) -> bool:
    """Return True if the given PID refers to a currently-running process."""
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        exit_code = ctypes.c_ulong(0)
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(h)
        return exit_code.value == STILL_ACTIVE
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True  # process exists, we just can't signal it
        except OSError:
            return False


def _acquire_instance_lock(substrate_path: str) -> Path:
    """Exit if another entity-watcher is already running for this workspace.

    Writes a PID file at _system/entity-watcher.pid. If the file already
    exists and refers to a live process, logs and exits immediately. If the
    PID is stale (process gone), overwrites with our PID and continues.

    Returns the PID file path so the caller can delete it on exit.
    """
    pid_file = Path(substrate_path) / "_system" / "entity-watcher.pid"
    try:
        if pid_file.exists():
            try:
                existing_pid = int(pid_file.read_text().strip())
                if _pid_is_running(existing_pid):
                    logging.info(
                        "entity-watcher already running (PID %s) — exiting", existing_pid
                    )
                    sys.exit(0)
            except (ValueError, OSError):
                pass  # Stale or corrupt PID file — overwrite below
        pid_file.write_text(str(os.getpid()))
    except Exception as e:
        logging.warning("instance-lock error: %s", e)
    return pid_file


def _configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [entity-watcher] %(levelname)s %(message)s",
        stream=sys.stderr,
    )


def _parse_entity_from_path(path_str, substrate_path):
    """Return (entity_type, entity_id, filename) if path is an entity content
    file we care about; otherwise None. Paths are normalized relative to
    substrate_path before matching so the regex works for both absolute and
    relative paths."""
    try:
        rel = os.path.relpath(path_str, substrate_path)
    except ValueError:
        return None  # different drive on Windows
    # watchdog emits paths with OS separators; normalize to forward slashes for regex.
    rel = rel.replace(os.sep, "/")
    m = ENTITY_PATH_RE.search(rel)
    if not m:
        return None
    entity_type = m.group(1)
    entity_id = m.group(4)
    filename = m.group(5)
    # Filter sidecars / hidden / meta.yaml.
    basename = os.path.basename(filename)
    if basename in IGNORED_BASENAMES:
        return None
    if any(basename.startswith(p) for p in IGNORED_PREFIXES):
        return None
    return entity_type, entity_id, filename


class EntityEventHandler(FileSystemEventHandler):
    """Accumulates entity-touching events, deferring the actual write to a
    background flusher that debounces per entity. Keeping the handler thin
    (no I/O) minimizes the chance that a slow disk turn blocks the event queue.
    """

    def __init__(self, substrate_path, pending, pending_lock):
        super().__init__()
        self.substrate_path = substrate_path
        # pending: dict[entity_id] -> (first_seen_ts, entity_type, set of filenames).
        # The filename set collects ALL content files touched for this entity
        # within the debounce window, so the changelog entry accurately
        # reports them rather than clobbering to whatever came last.
        self.pending = pending
        self.pending_lock = pending_lock

    def _queue(self, src_path):
        parsed = _parse_entity_from_path(src_path, self.substrate_path)
        if not parsed:
            return
        entity_type, entity_id, filename = parsed
        now = time.monotonic()
        with self.pending_lock:
            # Keep the earliest-seen timestamp so the debounce window counts
            # from the first event; add this filename to the set.
            existing = self.pending.get(entity_id)
            if existing is None:
                self.pending[entity_id] = (now, entity_type, {filename})
            else:
                first_seen, _, filenames = existing
                filenames.add(filename)
                # entity_type is invariant per entity_id; keep the existing one.
                self.pending[entity_id] = (first_seen, entity_type, filenames)

    # We react to creates, modifies, and renames (dest side). Deletes don't
    # warrant a last_edited bump — the content is gone, not changed.
    def on_modified(self, event):
        if event.is_directory:
            return
        self._queue(event.src_path)

    def on_created(self, event):
        if event.is_directory:
            return
        self._queue(event.src_path)

    def on_moved(self, event):
        if event.is_directory:
            return
        # Destination is the "new" content; treat as modified.
        self._queue(event.dest_path)


def _flush_pending(pending, pending_lock, substrate_path, db_path):
    """Apply debounced entity-touch events whose debounce window has elapsed.

    Runs on a timer thread. For each pending entity whose first-seen timestamp
    is older than DEBOUNCE_SECONDS, writes the bump and removes it from the
    pending set. Any events that arrived within the window are coalesced
    into this single bump and their filenames collected in the changelog entry.

    Failure handling: if _bump_last_edited raises (SQLite locked past the
    30s timeout, disk full, permissions), the entity has already been
    removed from `pending` and we log + move on. The entity's last_edited
    stays stale until the next edit bumps it. This is eventual consistency —
    the alternative (re-queueing with unbounded retry) risks memory growth
    on persistent failures.
    """
    now = time.monotonic()
    due = []
    with pending_lock:
        for entity_id, (first_seen, entity_type, filenames) in list(pending.items()):
            if now - first_seen >= DEBOUNCE_SECONDS:
                due.append((entity_id, entity_type, filenames))
                del pending[entity_id]

    for entity_id, entity_type, filenames in due:
        try:
            _bump_last_edited(entity_id, entity_type, filenames, substrate_path, db_path)
        except Exception as e:
            # Don't let one bad entity kill the watcher. Log and move on.
            logging.warning("bump failed for %s (%s): %s", entity_id, sorted(filenames), e)


def _bump_last_edited(entity_id, entity_type, filenames, substrate_path, db_path):
    """Update last_edited in SQLite + meta.yaml, and log the change.

    `filenames` is a set of content filenames that were touched within the
    debounce window. Used to populate the changelog entry accurately when
    multiple files in the same entity changed between flushes.

    Failure mode: if this function raises (SQLite locked, disk full,
    permissions), the caller catches and logs the error but does NOT re-queue
    the entity. last_edited becomes eventually consistent — the next content
    edit bumps it again. A brief stale-timestamp window is acceptable; silent
    loss of this specific changelog entry is not but also hard to avoid without
    an unbounded retry mechanism.
    """
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # timeout=30 matches fileio.LOCK_TIMEOUT_SECONDS — under heavy write load
    # (e.g., during migrate-to-sqlite.py rebuilds) the DB briefly holds a
    # write lock. Python's default busy-timeout is 5s, which is too tight for
    # multi-minute migrate runs on a growing graph. A 30s ceiling keeps us
    # from wedging forever while still absorbing normal-write contention.
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        row = conn.execute(
            "SELECT path, name FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        if not row:
            # Entity not indexed (e.g., just created but migrate hasn't run yet,
            # or path is outside the real graph). Skip silently.
            return
        entity_path, entity_name = row

        conn.execute(
            "UPDATE entities SET last_edited = ? WHERE id = ?", (now_iso, entity_id)
        )
        conn.commit()
    finally:
        conn.close()

    meta_path = os.path.join(substrate_path, entity_path, "meta.yaml")
    if os.path.exists(meta_path):
        quoted_now = quote_yaml_scalar(now_iso)
        with safe_write(meta_path) as (content, write):
            new_content, n = re.subn(
                r"^last_edited:.*$",
                f"last_edited: {quoted_now}",
                content,
                flags=re.MULTILINE,
            )
            if n == 0:
                new_content = content.rstrip("\n") + f"\nlast_edited: {quoted_now}\n"
            write(new_content)

    # Log to changelog. Shape depends on how many files were touched in the
    # debounce window:
    #   - Single file: `{attribute: content, file: <name>}` — matches the
    #     legacy hook's shape, so readers that parse `file:` keep working.
    #   - Multiple files: `{attribute: content, files: [<names>]}` — honestly
    #     represents all the files that changed within the coalesce window.
    file_list = sorted(filenames)
    if len(file_list) == 1:
        change_entry = {"attribute": "content", "file": file_list[0]}
        log_desc = file_list[0]
    else:
        change_entry = {"attribute": "content", "files": file_list}
        log_desc = f"{len(file_list)} files: {', '.join(file_list)}"
    log_change(
        operation="update",
        entity_id=entity_id,
        entity_type=entity_type,
        entity_name=entity_name,
        changes=[change_entry],
    )
    logging.info("bumped last_edited for %s (%s via %s)", entity_id, entity_type, log_desc)


def main():
    _configure_logging()

    substrate_path = os.environ.get(
        "SUBSTRATE_PATH",
        os.path.dirname(os.path.dirname(SCRIPT_DIR)),
    )
    pid_file = _acquire_instance_lock(substrate_path)
    entities_root = os.path.join(substrate_path, "entities")
    db_path = os.path.join(substrate_path, "_system", "index", "substrate.db")

    if not os.path.isdir(entities_root):
        logging.error("entities/ not found at %s — is SUBSTRATE_PATH correct?", entities_root)
        sys.exit(1)

    logging.info("starting entity-watcher")
    logging.info("  substrate: %s", substrate_path)
    logging.info("  entities:  %s", entities_root)
    logging.info("  db:        %s", db_path)

    pending = {}
    pending_lock = threading.Lock()

    handler = EntityEventHandler(substrate_path, pending, pending_lock)
    observer = Observer()
    observer.schedule(handler, entities_root, recursive=True)
    observer.start()

    # Graceful shutdown on SIGTERM/SIGINT — let service managers stop us cleanly.
    stop_event = threading.Event()

    def _stop(signum, _frame):
        logging.info("received signal %s, stopping", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    try:
        # Flush loop runs until we receive a stop signal.
        while not stop_event.is_set():
            stop_event.wait(timeout=FLUSH_INTERVAL_SECONDS)
            _flush_pending(pending, pending_lock, substrate_path, db_path)
    finally:
        observer.stop()
        observer.join(timeout=5)
        # Final flush on shutdown — apply any remaining pending entries.
        _flush_pending(pending, pending_lock, substrate_path, db_path)
        logging.info("entity-watcher stopped")
        try:
            pid_file.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
