"""SQLite connection utility with stale-shm auto-recovery."""

import os
import sqlite3


def open_db(path: str, **kwargs) -> sqlite3.Connection:
    """Open a SQLite database, auto-recovering from a stale .shm file.

    A stale shared-memory file can be left behind when a process holding the
    database is killed before it can clean up. SQLite normally recovers from
    this automatically, but in certain scenarios (WAL mode on macOS, specific
    timing of checkpoints vs process death) the first query fails with
    'disk I/O error'. Note: sqlite3.connect() itself succeeds — the error
    surfaces on first execute(), so that is where we catch and recover.

    Recovery is safe only when the WAL file is empty (no uncommitted
    transactions). If the WAL is non-empty, the error is re-raised — that
    requires `substrate index rebuild`.
    """
    conn = sqlite3.connect(path, **kwargs)
    try:
        conn.execute("SELECT 1")
        return conn
    except sqlite3.OperationalError as e:
        if "disk I/O error" not in str(e):
            conn.close()
            raise
        conn.close()
        wal_path = path + "-wal"
        shm_path = path + "-shm"
        wal_empty = not os.path.exists(wal_path) or os.path.getsize(wal_path) == 0
        if wal_empty and os.path.exists(shm_path):
            os.remove(shm_path)
            return sqlite3.connect(path, **kwargs)
        raise
