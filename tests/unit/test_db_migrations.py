from __future__ import annotations

import pytest

from atc.state.db import _SCHEMA_SQL, _apply_file_migrations, _ensure_schema_migrations_table, _list_applied_migrations, get_connection


@pytest.mark.asyncio
async def test_apply_file_migrations_skips_015_when_provider_column_already_exists() -> None:
    async with get_connection(":memory:") as db:
        await db.executescript(_SCHEMA_SQL)
        await db.commit()

        await _apply_file_migrations(db)

        applied = await _list_applied_migrations(db)
        assert "015_session_provider_scope.sql" in applied
