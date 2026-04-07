import pytest
import sqlite3


class TestDatabaseOperations:
    """Tests for database schema and symbol sync logic."""

    def test_project_schema_creates_all_tables(self, project_db):
        cursor = project_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]
        assert "system_config" in tables
        assert "sessions" in tables
        assert "events" in tables
        assert "symbols" in tables

    def test_schema_version_is_set(self, project_db):
        cursor = project_db.execute(
            "SELECT value FROM system_config WHERE key='schema_version'"
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "1"

    def test_symbol_insert_and_query(self, project_db):
        project_db.execute(
            "INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.py", "process", "function", "def process()", "Docs", 0, 5),
        )
        project_db.commit()
        cursor = project_db.execute(
            "SELECT name, kind FROM symbols WHERE file_path='test.py'"
        )
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0] == ("process", "function")

    def test_symbol_unique_constraint(self, project_db):
        project_db.execute(
            "INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.py", "process", "function", "def process()", "", 0, 5),
        )
        project_db.commit()
        # Inserting same (file_path, name, kind) should conflict
        with pytest.raises(sqlite3.IntegrityError):
            project_db.execute(
                "INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test.py", "process", "function", "def process(x)", "", 0, 6),
            )

    def test_symbol_sync_deletes_stale(self, project_db):
        """Simulates sync_symbols_for_file: insert, then delete stale, upsert current."""
        # Initial state: two symbols
        project_db.execute(
            "INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.py", "old_func", "function", "", "", 0, 1),
        )
        project_db.execute(
            "INSERT INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test.py", "keep_func", "function", "", "", 2, 3),
        )
        project_db.commit()

        # After re-parse: old_func is gone, keep_func remains, new_func added
        current_symbols = {("keep_func", "function"), ("new_func", "function")}

        # Delete symbols not in current set
        existing = project_db.execute(
            "SELECT name, kind FROM symbols WHERE file_path='test.py'"
        ).fetchall()
        for name, kind in existing:
            if (name, kind) not in current_symbols:
                project_db.execute(
                    "DELETE FROM symbols WHERE file_path=? AND name=? AND kind=?",
                    ("test.py", name, kind),
                )

        # Upsert current
        for name, kind in current_symbols:
            project_db.execute(
                "INSERT OR REPLACE INTO symbols (file_path, name, kind, signature, docstring, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("test.py", name, kind, "", "", 0, 1),
            )
        project_db.commit()

        rows = project_db.execute(
            "SELECT name FROM symbols WHERE file_path='test.py' ORDER BY name"
        ).fetchall()
        names = [r[0] for r in rows]
        assert "old_func" not in names
        assert "keep_func" in names
        assert "new_func" in names

    def test_session_insert(self, project_db):
        project_db.execute(
            "INSERT INTO sessions (started_at) VALUES (?)",
            ("2025-01-01 00:00:00",),
        )
        project_db.commit()
        row = project_db.execute("SELECT id, started_at FROM sessions").fetchone()
        assert row is not None
        assert row[0] > 0
        assert row[1] == "2025-01-01 00:00:00"
