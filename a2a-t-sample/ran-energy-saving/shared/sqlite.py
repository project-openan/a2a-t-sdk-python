"""Shared SQLite access for the sample stores.

Both databases use the same connection settings (WAL, busy timeout, one connection per operation)
and the same migration convention: ``migrations[i]`` upgrades the schema from version ``i`` to
``i + 1`` and the applied version is stored in ``PRAGMA user_version``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final

#: SQLite busy timeout in milliseconds.
BUSY_TIMEOUT_MS: Final[int] = 5000

#: One schema upgrade from version ``index`` to ``index + 1``.
SchemaMigration = Callable[[sqlite3.Connection], None]


@contextmanager
def connect(path: str | Path) -> Generator[sqlite3.Connection, None, None]:
    """Open one writable connection, commit on success, and always close it."""
    connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000.0)
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode=WAL")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def readonly_connection(path: str | Path) -> sqlite3.Connection | None:
    """Open one read-only connection; ``None`` when the database file does not exist.

    The call never creates the file, so a read helper can be used for a not-yet-created database.
    """
    database = Path(path).expanduser()
    if not database.is_file():
        return None
    return sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)


def schema_version(connection: sqlite3.Connection) -> int:
    """Return the schema version stored in ``PRAGMA user_version``."""
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row is not None else 0


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[SchemaMigration],
) -> int:
    """Apply the migrations newer than the stored version and return the resulting version.

    Raises:
        RuntimeError: when the database was written by a newer code version.
    """
    current = schema_version(connection)
    latest = len(migrations)
    if current > latest:
        raise RuntimeError(
            f"Database schema version {current} is newer than this build supports ({latest})."
        )
    for version in range(current + 1, latest + 1):
        migrations[version - 1](connection)
        connection.execute(f"PRAGMA user_version={version}")
    connection.commit()
    return latest
