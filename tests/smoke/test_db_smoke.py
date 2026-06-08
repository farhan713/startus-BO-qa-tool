"""DB-layer smoke test — proves the framework can reach SQL Server."""
from __future__ import annotations

import pytest

from framework.db import SqlServerHelper


@pytest.mark.smoke
@pytest.mark.db
def test_database_is_reachable(db: SqlServerHelper) -> None:
    assert db.ping(), "SQL Server is not reachable with configured credentials."


@pytest.mark.smoke
@pytest.mark.db
def test_core_tables_exist(db: SqlServerHelper) -> None:
    """At least one well-known Stratus table should be present.

    Adjust the candidate list once a real DB is wired up — these names
    come from Hibernate entity mappings (Employee, Store, Receipt).
    """
    candidates = ("Employee", "Store", "Receipt")
    found = db.fetch_all(
        """
        SELECT TABLE_NAME
          FROM INFORMATION_SCHEMA.TABLES
         WHERE TABLE_TYPE = 'BASE TABLE'
           AND TABLE_NAME IN (?, ?, ?)
        """,
        candidates,
    )
    assert found, (
        f"None of {candidates} exist in the configured DB — "
        "either wrong DB or schema not deployed."
    )
