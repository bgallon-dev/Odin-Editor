"""Class with several methods — tests the drafter's class awareness."""
from typing import List
import sqlite3


class SymbolIndex:
    """Maintains an index of symbols extracted from source files."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS symbols (
                id        INTEGER PRIMARY KEY,
                file_path TEXT NOT NULL,
                name      TEXT NOT NULL,
                kind      TEXT NOT NULL,
                UNIQUE(file_path, name)
            )
        """)
        self.conn.commit()

    def upsert(self, file_path: str, name: str, kind: str) -> None:
        """Insert or update a symbol record."""
