"""SQLite connection helpers.

Kept separate so every module opens the warehouse the same way, with foreign
keys on. SQLite does not enforce foreign keys unless you ask it to, per
connection, which is a quiet trap.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or config.DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


@contextmanager
def transaction(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def apply_sql_file(con: sqlite3.Connection, sql_path: Path) -> None:
    con.executescript(sql_path.read_text())


def init_schema(path: Path | None = None) -> None:
    with transaction(path) as con:
        apply_sql_file(con, config.SQL_DIR / "01_schema.sql")


def table_count(con: sqlite3.Connection, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
