"""System long-term memory — cross-project learnings stored in tower_memory.

Supports two search modes:
  - Semantic (cosine similarity over float32 embeddings stored as BLOBs)
  - FTS5 full-text search via ``tower_memory_fts`` virtual table

Embeddings are generated lazily: if the ``ANTHROPIC_API_KEY`` or an
OpenAI-compatible embedding endpoint is unavailable, ``embedding`` is stored
as ``None`` and all searches fall back to FTS5.

Usage::

    entry_id = await LongTermMemory.write(db, "auth-pattern", "Always use JWT...")
    results  = await LongTermMemory.search_semantic(db, "authentication")
    results  = await LongTermMemory.search_fts(db, "JWT")
"""

from __future__ import annotations

import json
import logging
import struct
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import aiosqlite

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _encode_embedding(values: list[float]) -> bytes:
    """Encode a list of float32 values as a packed BLOB."""
    return struct.pack(f"{len(values)}f", *values)


def _decode_embedding(blob: bytes) -> list[float]:
    """Decode a packed float32 BLOB back to a list of floats."""
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


async def _generate_embedding(text: str) -> list[float] | None:
    """Generate a text embedding.

    Returns ``None`` if no embedding service is configured.  When numpy and
    an OpenAI-compatible endpoint are available this can be wired up to
    produce real vectors; for now it gracefully degrades to FTS-only search.
    """
    # Soft-fail: embedding generation requires an OpenAI-compatible endpoint.
    # Return None to trigger FTS fallback — no hard dependency on openai SDK.
    return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length float vectors."""
    try:
        import numpy as np

        va = np.array(a, dtype=np.float32)
        vb = np.array(b, dtype=np.float32)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    except ImportError:
        # numpy not available — fall back to pure Python
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = sum(x * x for x in a) ** 0.5
        mag_b = sum(x * x for x in b) ** 0.5
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Result dataclass (matches tower_memory schema + embedding field)
# ---------------------------------------------------------------------------


class TowerMemoryRecord:
    """Lightweight result object for LTM search results."""

    __slots__ = ("id", "key", "value", "project_id", "created_at", "updated_at", "embedding")

    def __init__(
        self,
        *,
        id: str,
        key: str,
        value: str,
        project_id: str | None,
        created_at: str,
        updated_at: str,
        embedding: bytes | None = None,
    ) -> None:
        self.id = id
        self.key = key
        self.value = value
        self.project_id = project_id
        self.created_at = created_at
        self.updated_at = updated_at
        self.embedding = embedding

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "key": self.key,
            "value": self.value,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> TowerMemoryRecord:
        d = dict(row)
        return cls(
            id=d["id"],
            key=d["key"],
            value=d["value"],
            project_id=d.get("project_id"),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            embedding=d.get("embedding"),
        )


# ---------------------------------------------------------------------------
# LongTermMemory
# ---------------------------------------------------------------------------


class LongTermMemory:
    """System LTM backed by ``tower_memory`` + FTS5 ``tower_memory_fts``."""

    @staticmethod
    async def write(
        db: aiosqlite.Connection,
        key: str,
        value: str,
        *,
        project_id: str | None = None,
    ) -> str:
        """Write (upsert) a long-term memory entry and return its id.

        Also syncs the FTS5 index and stores an embedding BLOB if one can be
        generated.  The upsert is on the ``key`` UNIQUE constraint.
        """
        now = _now()

        # Attempt to generate embedding (may return None)
        embedding_floats = await _generate_embedding(value)
        embedding_blob = _encode_embedding(embedding_floats) if embedding_floats else None

        # Check if key already exists (for FTS sync)
        cursor = await db.execute(
            "SELECT id FROM tower_memory WHERE key = ?", (key,)
        )
        existing = await cursor.fetchone()

        if existing is not None:
            entry_id = str(existing["id"])
            await db.execute(
                """UPDATE tower_memory
                   SET value = ?, project_id = ?, embedding = ?, updated_at = ?
                   WHERE key = ?""",
                (value, project_id, embedding_blob, now, key),
            )
            # Remove old FTS entry and re-insert
            await db.execute(
                "DELETE FROM tower_memory_fts WHERE memory_id = ?", (entry_id,)
            )
        else:
            entry_id = str(uuid.uuid4())
            await db.execute(
                """INSERT INTO tower_memory (id, key, value, project_id, embedding, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (entry_id, key, value, project_id, embedding_blob, now, now),
            )

        # Sync FTS5 index
        await db.execute(
            "INSERT INTO tower_memory_fts(memory_id, key, value) VALUES (?, ?, ?)",
            (entry_id, key, value),
        )
        await db.commit()

        logger.debug("LTM write: key=%r id=%s", key, entry_id)
        return entry_id

    @staticmethod
    async def delete(db: aiosqlite.Connection, key: str) -> bool:
        """Delete an LTM entry by key.  Returns True if a row was deleted."""
        cursor = await db.execute(
            "SELECT id FROM tower_memory WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        if row is None:
            return False
        entry_id = str(row["id"])
        await db.execute("DELETE FROM tower_memory WHERE key = ?", (key,))
        await db.execute(
            "DELETE FROM tower_memory_fts WHERE memory_id = ?", (entry_id,)
        )
        await db.commit()
        logger.debug("LTM deleted: key=%r", key)
        return True

    @staticmethod
    async def search_semantic(
        db: aiosqlite.Connection,
        query: str,
        limit: int = 10,
        *,
        project_id: str | None = None,
    ) -> list[TowerMemoryRecord]:
        """Semantic search using cosine similarity.

        If embeddings are available, ranks results by cosine similarity to the
        query embedding.  Falls back to :meth:`search_fts` when no embeddings
        are stored.
        """
        # Load all entries (with embeddings) into memory
        if project_id is not None:
            cursor = await db.execute(
                "SELECT * FROM tower_memory WHERE project_id = ? OR project_id IS NULL",
                (project_id,),
            )
        else:
            cursor = await db.execute("SELECT * FROM tower_memory")

        rows = await cursor.fetchall()
        records = [TowerMemoryRecord.from_row(r) for r in rows]

        # Check if any embeddings exist
        has_embeddings = any(r.embedding is not None for r in records)
        if not has_embeddings:
            return await LongTermMemory.search_fts(db, query, limit, project_id=project_id)

        # Generate query embedding
        query_floats = await _generate_embedding(query)
        if query_floats is None:
            return await LongTermMemory.search_fts(db, query, limit, project_id=project_id)

        # Rank by cosine similarity
        scored: list[tuple[float, TowerMemoryRecord]] = []
        for rec in records:
            if rec.embedding is not None:
                doc_floats = _decode_embedding(rec.embedding)
                sim = _cosine_similarity(query_floats, doc_floats)
                scored.append((sim, rec))

        scored.sort(key=lambda t: t[0], reverse=True)
        return [rec for _, rec in scored[:limit]]

    @staticmethod
    async def search_fts(
        db: aiosqlite.Connection,
        query: str,
        limit: int = 10,
        *,
        project_id: str | None = None,
    ) -> list[TowerMemoryRecord]:
        """Full-text search over tower_memory via the FTS5 virtual table.

        Results are ranked by BM25 relevance (FTS5 default).
        """
        try:
            if project_id is not None:
                cursor = await db.execute(
                    """SELECT tm.*
                       FROM tower_memory tm
                       WHERE tm.id IN (
                           SELECT memory_id FROM tower_memory_fts
                           WHERE tower_memory_fts MATCH ?
                           ORDER BY rank
                           LIMIT ?
                       )
                       AND (tm.project_id = ? OR tm.project_id IS NULL)""",
                    (query, limit, project_id),
                )
            else:
                cursor = await db.execute(
                    """SELECT tm.*
                       FROM tower_memory tm
                       WHERE tm.id IN (
                           SELECT memory_id FROM tower_memory_fts
                           WHERE tower_memory_fts MATCH ?
                           ORDER BY rank
                           LIMIT ?
                       )""",
                    (query, limit),
                )
            rows = await cursor.fetchall()
            return [TowerMemoryRecord.from_row(r) for r in rows]
        except Exception:
            logger.exception("FTS search failed for query=%r, returning empty", query)
            return []

    @staticmethod
    async def list_all(
        db: aiosqlite.Connection,
        *,
        project_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[TowerMemoryRecord]:
        """Return LTM entries, optionally filtered by project, with pagination."""
        if project_id is not None:
            cursor = await db.execute(
                """SELECT * FROM tower_memory WHERE project_id = ?
                   ORDER BY updated_at DESC LIMIT ? OFFSET ?""",
                (project_id, limit, offset),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM tower_memory ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        rows = await cursor.fetchall()
        return [TowerMemoryRecord.from_row(r) for r in rows]

    @staticmethod
    async def get(db: aiosqlite.Connection, key: str) -> TowerMemoryRecord | None:
        """Fetch a single LTM entry by key."""
        cursor = await db.execute("SELECT * FROM tower_memory WHERE key = ?", (key,))
        row = await cursor.fetchone()
        return TowerMemoryRecord.from_row(row) if row is not None else None

    @staticmethod
    async def value_as_dict(record: TowerMemoryRecord) -> dict[str, object]:
        """Parse the JSON value field, returning a dict (or wrapping in one)."""
        try:
            parsed = json.loads(record.value)
            if isinstance(parsed, dict):
                return parsed  # type: ignore[return-value]
            return {"value": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"value": record.value}
