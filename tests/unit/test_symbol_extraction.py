import pytest
from kettle_server import extract_symbols


class TestSymbolExtraction:
    """Tests for the AST-based symbol extraction in kettle_server.py."""

    def test_extracts_top_level_function(self):
        source = "def process(items, limit=10):\n    return items[:limit]\n"
        symbols = extract_symbols("test.py", source)
        names = [s.name for s in symbols]
        assert "process" in names

    def test_extracts_class(self):
        source = "class DataProcessor:\n    pass\n"
        symbols = extract_symbols("test.py", source)
        names = [s.name for s in symbols]
        assert "DataProcessor" in names

    def test_extracts_method_inside_class(self):
        source = "class Foo:\n    def bar(self):\n        pass\n"
        symbols = extract_symbols("test.py", source)
        names = [s.name for s in symbols]
        assert "Foo.bar" in names

    def test_captures_line_numbers(self):
        source = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        symbols = extract_symbols("test.py", source)
        foo = next(s for s in symbols if s.name == "foo")
        bar = next(s for s in symbols if s.name == "bar")
        assert foo.line_start < bar.line_start

    def test_captures_docstring(self):
        source = 'def foo():\n    """Does something useful."""\n    pass\n'
        symbols = extract_symbols("test.py", source)
        foo = next(s for s in symbols if s.name == "foo")
        assert "Does something useful" in foo.docstring

    def test_syntax_error_returns_empty(self):
        symbols = extract_symbols("test.py", "def broken(")
        assert symbols == []

    def test_empty_file_returns_empty(self):
        symbols = extract_symbols("test.py", "")
        assert symbols == []

    def test_extracts_imports(self):
        source = "import os\nfrom pathlib import Path\n"
        symbols = extract_symbols("test.py", source)
        names = [s.name for s in symbols]
        assert "os" in names
        assert "Path" in names

    def test_captures_function_signature(self):
        source = "def process(items: list, limit: int = 10) -> list:\n    pass\n"
        symbols = extract_symbols("test.py", source)
        proc = next(s for s in symbols if s.name == "process")
        assert len(proc.signature) > 0

    def test_async_function_extracted(self):
        source = "async def fetch(url: str):\n    pass\n"
        symbols = extract_symbols("test.py", source)
        names = [s.name for s in symbols]
        assert "fetch" in names
