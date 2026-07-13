"""
Conversation Store

Persists conversation history in PostgreSQL for cross-session memory.
Uses the same pgvector database instance configured in the RAG system.
"""
import logging
from typing import List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class ConversationStore:
    """
    Stores and retrieves conversation history from PostgreSQL.
    Uses psycopg for direct DB access (the connection string is already available).
    """

    TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS conversations (
        id SERIAL PRIMARY KEY,
        session_id VARCHAR(128) NOT NULL,
        role VARCHAR(16) NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """

    INDEX_DDL = """
    CREATE INDEX IF NOT EXISTS idx_conv_session
    ON conversations(session_id, created_at);
    """

    def __init__(self, conn_string: str):
        """
        Initialize with a PostgreSQL connection string.
        Normalizes the connection string for psycopg compatibility.
        """
        # Normalize connection string
        self.conn_string = conn_string
        if "postgresql+psycopg2://" in self.conn_string:
            self.conn_string = self.conn_string.replace(
                "postgresql+psycopg2://", "postgresql://"
            )

        self._ensure_table()

    def _get_conn(self):
        """Get a new psycopg connection."""
        import psycopg
        return psycopg.connect(self.conn_string)

    def _ensure_table(self) -> None:
        """Create the conversations table if it doesn't exist."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(self.TABLE_DDL)
                    cur.execute(self.INDEX_DDL)
                conn.commit()
            logger.info("Conversations table ensured")
        except Exception as e:
            logger.error(f"Failed to create conversations table: {e}")

    def save_message(self, session_id: str, role: str, content: str) -> None:
        """Save a single message to the conversation history."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO conversations (session_id, role, content) "
                        "VALUES (%s, %s, %s)",
                        (session_id, role, content),
                    )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to save message: {e}")

    def get_history(
        self, session_id: str, limit: int = 50
    ) -> List[Tuple[str, str]]:
        """
        Retrieve conversation history for a session.
        Returns list of (role, content) tuples in chronological order.
        """
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT role, content FROM ("
                        "SELECT id, role, content, created_at FROM conversations "
                        "WHERE session_id = %s "
                        "ORDER BY created_at DESC, id DESC "
                        "LIMIT %s"
                        ") AS recent "
                        "ORDER BY created_at ASC, id ASC",
                        (session_id, limit),
                    )
                    return cur.fetchall()
        except Exception as e:
            logger.error(f"Failed to get history: {e}")
            return []

    def clear_session(self, session_id: str) -> None:
        """Delete all messages for a session."""
        try:
            with self._get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM conversations WHERE session_id = %s",
                        (session_id,),
                    )
                conn.commit()
            logger.info(f"Cleared session: {session_id}")
        except Exception as e:
            logger.error(f"Failed to clear session: {e}")
