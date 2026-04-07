import pytest
from pipeline import assemble_drafter_context, extract_context_snippet


class TestAssembleDrafterContext:

    def test_cursor_marker_inserted_at_offset(self):
        content = "def foo():\n    pass\n"
        result = assemble_drafter_context("test.py", content, 10)
        assert "<CURSOR>" in result
        # Cursor should be at offset 10
        cursor_pos = result.index("<CURSOR>")
        assert result[:cursor_pos] == content[:10]

    def test_cursor_at_start(self):
        content = "def foo(): pass"
        result = assemble_drafter_context("test.py", content, 0)
        assert result.startswith("<CURSOR>")

    def test_cursor_at_end(self):
        content = "def foo(): pass"
        result = assemble_drafter_context("test.py", content, len(content))
        assert result.endswith("<CURSOR>")

    def test_cursor_beyond_content_clamped(self):
        content = "short"
        result = assemble_drafter_context("test.py", content, 9999)
        assert "<CURSOR>" in result
        assert result.endswith("<CURSOR>")

    def test_negative_offset_clamped(self):
        content = "def foo(): pass"
        result = assemble_drafter_context("test.py", content, -5)
        assert result.startswith("<CURSOR>")

    def test_truncation_preserves_cursor(self):
        # Generate content larger than MAX_CHARS
        content = "x = 1\n" * 5000  # ~30000 chars
        offset = len(content) // 2
        result = assemble_drafter_context("test.py", content, offset)
        assert "<CURSOR>" in result
        assert len(result) <= 24_008  # MAX_CHARS + len("<CURSOR>")

    def test_empty_content(self):
        result = assemble_drafter_context("test.py", "", 0)
        assert result == "<CURSOR>"

    def test_cursor_marker_appears_exactly_once(self):
        content = "def foo():\n    bar()\n"
        result = assemble_drafter_context("test.py", content, 15)
        assert result.count("<CURSOR>") == 1


class TestExtractContextSnippet:

    def test_returns_first_n_lines(self):
        content = "\n".join(f"line {i}" for i in range(100))
        snippet = extract_context_snippet(content, max_lines=10)
        lines = snippet.split("\n")
        assert len(lines) == 10
        assert lines[0] == "line 0"
        assert lines[9] == "line 9"

    def test_short_content_returned_fully(self):
        content = "line 1\nline 2\nline 3"
        snippet = extract_context_snippet(content, max_lines=50)
        assert snippet == content

    def test_empty_content(self):
        snippet = extract_context_snippet("", max_lines=50)
        assert snippet == ""
