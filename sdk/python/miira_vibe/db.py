"""The app's SQLite database at VIBE_DB_PATH (default /data/app.sqlite)."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import _env


def connect() -> sqlite3.Connection:
    """Open the app database with sensible defaults (WAL, foreign keys, dict-like rows)."""
    path = Path(_env.db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """`with db.session() as conn:` commits on success, rolls back on error, always closes."""
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()
