"""Tests of the shared SQLite helper and its migration runner."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from shared.sqlite import apply_migrations, connect, readonly_connection, schema_version


def test_connect_applies_wal_and_commits(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"

    with connect(database) as connection:
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        connection.execute("CREATE TABLE items (id TEXT PRIMARY KEY)")

    assert mode.lower() == "wal"
    with sqlite3.connect(database) as check:
        table = check.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'items'"
        ).fetchone()
    assert table is not None


def test_apply_migrations_runs_each_step_once(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"
    applied: list[int] = []

    def first(connection: sqlite3.Connection) -> None:
        connection.execute("CREATE TABLE first (id TEXT)")
        applied.append(1)

    def second(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE first ADD COLUMN value TEXT")
        applied.append(2)

    with connect(database) as connection:
        assert schema_version(connection) == 0
        assert apply_migrations(connection, (first, second)) == 2
        assert schema_version(connection) == 2
        assert apply_migrations(connection, (first, second)) == 2

    assert applied == [1, 2]


def test_apply_migrations_rejects_a_newer_database(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"
    with connect(database) as connection:
        connection.execute("PRAGMA user_version=3")
        with pytest.raises(RuntimeError, match="newer"):
            apply_migrations(connection, ())


def test_readonly_connection_never_creates_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    assert readonly_connection(missing) is None
    assert not missing.exists()


def test_readonly_connection_reads_an_existing_database(tmp_path: Path) -> None:
    database = tmp_path / "store.sqlite3"
    with connect(database) as connection:
        connection.execute("CREATE TABLE items (id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO items VALUES ('a')")

    connection = readonly_connection(database)
    assert connection is not None
    try:
        rows = connection.execute("SELECT id FROM items").fetchall()
    finally:
        connection.close()
    assert rows == [("a",)]
