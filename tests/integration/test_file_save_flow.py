import pytest
import sqlite3


pytestmark = [pytest.mark.slow]


class TestFileSaveFlow:
    """Tests the file save -> symbol extraction -> DB sync flow."""

    def test_extract_and_store_symbols(self, project_db, sample_python_file):
        """Full flow: extract symbols from a file and store in the DB."""
        from kettle_server import extract_symbols

        path, content = sample_python_file
        symbols = extract_symbols(str(path), content)

        assert len(symbols) > 0

        for sym in symbols:
            project_db.execute(
                "INSERT OR REPLACE INTO symbols "
                "(file_path, name, kind, signature, docstring, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym.file_path, sym.name, sym.kind, sym.signature,
                 sym.docstring, sym.line_start, sym.line_end),
            )
        project_db.commit()

        rows = project_db.execute(
            "SELECT name, kind FROM symbols WHERE file_path=?", (str(path),)
        ).fetchall()
        names = [r[0] for r in rows]
        assert "process_items" in names
        assert "DataProcessor" in names

    def test_re_extract_removes_stale_symbols(self, project_db, tmp_path):
        """When a function is removed from a file, its symbol should be deleted."""
        from kettle_server import extract_symbols

        file_path = tmp_path / "changing.py"

        # Version 1: two functions
        v1 = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        file_path.write_text(v1)
        syms1 = extract_symbols(str(file_path), v1)
        for sym in syms1:
            project_db.execute(
                "INSERT OR REPLACE INTO symbols "
                "(file_path, name, kind, signature, docstring, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym.file_path, sym.name, sym.kind, sym.signature,
                 sym.docstring, sym.line_start, sym.line_end),
            )
        project_db.commit()

        # Version 2: bar removed, baz added
        v2 = "def foo():\n    pass\n\ndef baz():\n    pass\n"
        file_path.write_text(v2)
        syms2 = extract_symbols(str(file_path), v2)

        # Sync: delete stale, upsert current
        current = {(s.name, s.kind) for s in syms2}
        existing = project_db.execute(
            "SELECT name, kind FROM symbols WHERE file_path=?", (str(file_path),)
        ).fetchall()
        for name, kind in existing:
            if (name, kind) not in current:
                project_db.execute(
                    "DELETE FROM symbols WHERE file_path=? AND name=? AND kind=?",
                    (str(file_path), name, kind),
                )
        for sym in syms2:
            project_db.execute(
                "INSERT OR REPLACE INTO symbols "
                "(file_path, name, kind, signature, docstring, line_start, line_end) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sym.file_path, sym.name, sym.kind, sym.signature,
                 sym.docstring, sym.line_start, sym.line_end),
            )
        project_db.commit()

        rows = project_db.execute(
            "SELECT name FROM symbols WHERE file_path=?", (str(file_path),)
        ).fetchall()
        names = [r[0] for r in rows]
        assert "foo" in names
        assert "baz" in names
        assert "bar" not in names
