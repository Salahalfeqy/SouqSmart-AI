import asyncio
import json
import time
import uuid
from typing import Any

from app.database import DEFAULT_DB_PATH, get_db_connection, now_utc_iso


class DatabaseSessionStore:
    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        ttl_seconds: float = 1800.0,
        turn_limit: int = 20,
        allow_dev_anonymous: bool = False,
    ) -> None:
        self.db_path = db_path
        self.ttl_seconds = ttl_seconds
        self.turn_limit = turn_limit
        self.allow_dev_anonymous = allow_dev_anonymous
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._lock_guard = asyncio.Lock()

    async def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        async with self._lock_guard:
            if session_id not in self._session_locks:
                self._session_locks[session_id] = asyncio.Lock()
            return self._session_locks[session_id]

    async def get_or_create(self, session_id: str | None, owner_hash: str | None = None) -> tuple[str, str]:
        now_epoch = time.time()
        now_iso = now_utc_iso()

        # Strict owner validation: reject missing, empty, whitespace, or literal "guest"
        clean_owner = (owner_hash or "").strip()
        is_invalid_owner = (not clean_owner or clean_owner.lower() == "guest")

        if is_invalid_owner:
            if not self.allow_dev_anonymous:
                raise ValueError("Missing or invalid owner identity: owner_hash is required and cannot be 'guest'")
            clean_owner = f"dev_anon_{uuid.uuid4().hex}"

        async with get_db_connection(self.db_path) as db:
            # 1. TTL cleanup: remove expired sessions and cascading turns
            await db.execute(
                "DELETE FROM chat_sessions WHERE (? - last_activity_epoch) > ?;",
                (now_epoch, self.ttl_seconds),
            )

            # 2. Check if requested session exists in database
            if session_id:
                async with db.execute(
                    "SELECT session_id, owner_hash, last_activity_epoch FROM chat_sessions WHERE session_id = ?;",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()

                if row:
                    stored_owner = row["owner_hash"]
                    # STRICT OWNER VALIDATION: Exact match only!
                    if stored_owner == clean_owner:
                        await db.execute(
                            """
                            UPDATE chat_sessions
                            SET last_activity_epoch = ?, updated_at = ?
                            WHERE session_id = ?;
                            """,
                            (now_epoch, now_iso, session_id),
                        )
                        await db.commit()
                        return session_id, "continued"
                    else:
                        # Owner mismatch: securely isolate by issuing a fresh server-generated session ID
                        new_id = str(uuid.uuid4())
                        await db.execute(
                            """
                            INSERT INTO chat_sessions (session_id, owner_hash, created_at, updated_at, last_activity_epoch)
                            VALUES (?, ?, ?, ?, ?);
                            """,
                            (new_id, clean_owner, now_iso, now_iso, now_epoch),
                        )
                        await db.commit()
                        return new_id, "new"

            # 3. Session does not exist (or no session_id provided):
            # MANDATORY SECURITY RULE: Always generate a fresh UUID server-side.
            # Never insert arbitrary client-provided session IDs into SQLite.
            new_id = str(uuid.uuid4())
            await db.execute(
                """
                INSERT INTO chat_sessions (session_id, owner_hash, created_at, updated_at, last_activity_epoch)
                VALUES (?, ?, ?, ?, ?);
                """,
                (new_id, clean_owner, now_iso, now_iso, now_epoch),
            )
            await db.commit()
            return new_id, "created"

    async def add_turn(
        self,
        session_id: str,
        user: str,
        assistant: str,
        rewritten_query: str | None = None,
        intent_type: str | None = None,
        referenced_product_ids: list[str] | None = None,
        retrieved_product_ids: list[str] | None = None,
        constraints: dict[str, Any] | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> int:
        now_epoch = time.time()
        now_iso = now_utc_iso()

        # Concurrency safety: acquire session lock to serialize concurrent turn additions
        lock = await self._get_session_lock(session_id)
        async with lock:
            async with get_db_connection(self.db_path) as db:
                await db.execute("BEGIN IMMEDIATE;")
                async with db.execute(
                    "SELECT COALESCE(MAX(turn_index), 0) as max_idx FROM chat_turns WHERE session_id = ?;",
                    (session_id,),
                ) as cursor:
                    row = await cursor.fetchone()
                    max_idx = row["max_idx"] if row else 0

                next_index = max_idx + 1

                await db.execute(
                    """
                    INSERT INTO chat_turns (
                        session_id, turn_index, user_message, rewritten_query,
                        assistant_reply, intent_type, referenced_product_ids_json,
                        retrieved_product_ids_json, constraints_json, sources_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        session_id,
                        next_index,
                        user,
                        rewritten_query,
                        assistant,
                        intent_type,
                        json.dumps(referenced_product_ids or []),
                        json.dumps(retrieved_product_ids or []),
                        json.dumps(constraints or {}),
                        json.dumps(sources or []),
                        now_iso,
                    ),
                )

                if next_index > self.turn_limit:
                    cutoff = next_index - self.turn_limit
                    await db.execute(
                        "DELETE FROM chat_turns WHERE session_id = ? AND turn_index <= ?;",
                        (session_id, cutoff),
                    )

                await db.execute(
                    """
                    UPDATE chat_sessions
                    SET last_activity_epoch = ?, updated_at = ?
                    WHERE session_id = ?;
                    """,
                    (now_epoch, now_iso, session_id),
                )
                await db.commit()
                return next_index

    async def get_turns(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        async with get_db_connection(self.db_path) as db:
            query = "SELECT * FROM chat_turns WHERE session_id = ? ORDER BY turn_index ASC;"
            async with db.execute(query, (session_id,)) as cursor:
                rows = await cursor.fetchall()

            turns = []
            for r in rows:
                turns.append(
                    {
                        "turn_index": r["turn_index"],
                        "user": r["user_message"],
                        "assistant": r["assistant_reply"],
                        "rewritten_query": r["rewritten_query"],
                        "intent_type": r["intent_type"],
                        "referenced_product_ids": json.loads(r["referenced_product_ids_json"] or "[]"),
                        "retrieved_product_ids": json.loads(r["retrieved_product_ids_json"] or "[]"),
                        "constraints": json.loads(r["constraints_json"] or "{}"),
                        "sources": json.loads(r["sources_json"] or "[]"),
                        "created_at": r["created_at"],
                    }
                )

            if limit is not None and len(turns) > limit:
                return turns[-limit:]
            return turns

    async def get_turn_count(self, session_id: str) -> int:
        async with get_db_connection(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM chat_turns WHERE session_id = ?;",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return int(row["cnt"]) if row else 0

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with get_db_connection(self.db_path) as db:
            async with db.execute(
                "SELECT * FROM chat_sessions WHERE session_id = ?;",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return {
                    "session_id": row["session_id"],
                    "owner_hash": row["owner_hash"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "last_activity_epoch": row["last_activity_epoch"],
                    "language": row["language"],
                    "metadata": json.loads(row["metadata_json"] or "{}"),
                }
