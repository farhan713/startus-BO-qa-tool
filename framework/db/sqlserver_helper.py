"""SQL Server access helper for verifying backend state after UI/API actions.

Uses pyodbc directly so the framework stays light. For complex fixtures
(test-data seeding, schema introspection), swap in SQLAlchemy.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pyodbc

from config import settings
from framework.utils import get_logger

log = get_logger("db")


class SqlServerHelper:
    def __init__(self, conn_str: str | None = None) -> None:
        self._conn_str = conn_str or settings.db.odbc_conn_str()
        self._conn: pyodbc.Connection | None = None

    # ------------------------------------------------------------ lifecycle

    def connect(self) -> pyodbc.Connection:
        if self._conn is None:
            log.info("connecting to SQL Server %s:%s/%s",
                     settings.db.host, settings.db.port, settings.db.name)
            self._conn = pyodbc.connect(self._conn_str, autocommit=False)
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def cursor(self) -> Iterator[pyodbc.Cursor]:
        cur = self.connect().cursor()
        try:
            yield cur
        finally:
            cur.close()

    # ------------------------------------------------------------- queries

    def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> tuple | None:
        with self.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return tuple(row) if row else None

    def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[tuple]:
        with self.cursor() as cur:
            cur.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()]

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.cursor() as cur:
            cur.execute(sql, params)
            rc = cur.rowcount
            self.connect().commit()
            return rc

    # ------------------------------------------------------------- helpers

    def ping(self) -> bool:
        try:
            return self.fetch_one("SELECT 1") == (1,)
        except pyodbc.Error as exc:
            log.warning("db ping failed: %s", exc)
            return False
