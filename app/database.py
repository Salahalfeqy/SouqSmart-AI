import os
import aiosqlite
from contextlib import asynccontextmanager
from datetime import datetime, timezone

DEFAULT_DB_PATH = os.path.join("data", "souqsmart_state.db")


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


async def init_database(db_path: str = DEFAULT_DB_PATH) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("PRAGMA foreign_keys = ON;")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                session_id TEXT PRIMARY KEY,
                owner_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_activity_epoch REAL NOT NULL,
                language TEXT NOT NULL DEFAULT 'auto',
                metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                rewritten_query TEXT,
                assistant_reply TEXT NOT NULL,
                intent_type TEXT,
                referenced_product_ids_json TEXT NOT NULL DEFAULT '[]',
                retrieved_product_ids_json TEXT NOT NULL DEFAULT '[]',
                constraints_json TEXT NOT NULL DEFAULT '{}',
                sources_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES chat_sessions(session_id) ON DELETE CASCADE
            );
            """
        )

        await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_turns_session ON chat_turns(session_id, turn_index);")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_turns_unique_turn ON chat_turns(session_id, turn_index);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_activity ON chat_sessions(last_activity_epoch);")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_chat_sessions_owner ON chat_sessions(owner_hash);")

        await db.commit()


@asynccontextmanager
async def get_db_connection(db_path: str = DEFAULT_DB_PATH):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA journal_mode = WAL;")
        await db.execute("PRAGMA busy_timeout = 5000;")
        await db.execute("PRAGMA foreign_keys = ON;")
        yield db
