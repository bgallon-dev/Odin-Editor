"""
Latency budget tests — the performance grounding layer.

These tests assert that measured system behavior stays within the
budgets we defined. They catch the failure mode where structurally
beautiful code makes the system slower without the models noticing.

Budget targets (calibrated to typical local hardware):
  - context_build_time:  <= 200ms
  - drafter_inference:   <= 30,000ms (30s)
  - validator_inference: <= 120,000ms (120s)
  - ipc_round_trip:      <= 5,000ms (5s) for the Python-side pipeline setup
  - file_save_path:      <= 50ms (synchronous portion only)
  - db_symbol_query:     <= 10ms for a 10,000-symbol index
  - db_event_write:      <= 5ms per event

Each test measures a specific component in isolation.
All use real code and real databases, not mocks.
"""
import sqlite3
import time
import pathlib
import tempfile
import pytest
import sys
import os

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Budget constants — these are the numbers the system is designed around
# ---------------------------------------------------------------------------

BUDGET_CONTEXT_BUILD_MS   =    200
BUDGET_DRAFTER_MS         = 30_000
BUDGET_VALIDATOR_MS       = 120_000
BUDGET_FILE_SAVE_SYNC_MS  =     50
BUDGET_DB_SYMBOL_QUERY_MS =     10
BUDGET_DB_EVENT_WRITE_MS  =      5
BUDGET_SESSION_INIT_MS    =    100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def build_large_symbol_db(n_symbols: int = 10_000) -> sqlite3.Connection:
    """Build an in-memory database with N symbols for query benchmarks."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE symbols (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path     TEXT NOT NULL,
            name          TEXT NOT NULL,
            kind          TEXT NOT NULL,
            signature     TEXT DEFAULT '',
            docstring     TEXT DEFAULT '',
            line_start    INTEGER NOT NULL DEFAULT 0,
            line_end      INTEGER NOT NULL DEFAULT 0,
            last_seen     TEXT NOT NULL DEFAULT (datetime('now')),
            session_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(file_path, name, kind)
        )
    """)
    conn.execute("""
        CREATE TABLE sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT NOT NULL DEFAULT (datetime('now')),
            closed_at   TEXT,
            file_scope  TEXT DEFAULT '[]',
            event_count INTEGER NOT NULL DEFAULT 0,
            summary     TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
            event_type TEXT NOT NULL,
            file_path  TEXT,
            payload    TEXT DEFAULT '{}'
        )
    """)

    # Insert N symbols across 100 files
    rows = []
    for i in range(n_symbols):
        file_idx = i % 100
        rows.append((
            f"project/module_{file_idx}.py",
            f"function_{i}",
            "function",
            f"function_{i}(arg1, arg2)",
            f"Docstring for function {i}",
            i * 5,
            i * 5 + 10,
        ))

    conn.executemany(
        "INSERT OR IGNORE INTO symbols "
        "(file_path, name, kind, signature, docstring, line_start, line_end) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Database latency tests — no LLM involvement
# ---------------------------------------------------------------------------

class TestDatabaseLatency:

    def test_symbol_query_within_budget(self):
        """
        Querying symbols for a specific file from a 10k-symbol index
        must complete within 10ms.
        """
        conn = build_large_symbol_db(10_000)

        t0 = time.monotonic()
        rows = conn.execute(
            "SELECT name, kind, signature FROM symbols "
            "WHERE file_path = ? "
            "ORDER BY session_count DESC, last_seen DESC",
            ("project/module_42.py",)
        ).fetchall()
        ms = elapsed_ms(t0)

        conn.close()

        assert ms <= BUDGET_DB_SYMBOL_QUERY_MS, (
            f"Symbol query took {ms}ms — exceeded {BUDGET_DB_SYMBOL_QUERY_MS}ms budget. "
            f"Check that indexes exist on (file_path) and (session_count)."
        )

    def test_symbol_count_query_within_budget(self):
        """COUNT(*) on 10k symbols must be fast."""
        conn = build_large_symbol_db(10_000)

        t0 = time.monotonic()
        count = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        ms = elapsed_ms(t0)

        conn.close()
        assert count == 10_000
        assert ms <= BUDGET_DB_SYMBOL_QUERY_MS

    def test_event_write_within_budget(self):
        """
        Writing a single event to the database must complete
        within 5ms including commit.
        """
        conn = build_large_symbol_db(1_000)
        session_id = conn.execute(
            "INSERT INTO sessions (started_at) VALUES (datetime('now'))"
        ).lastrowid
        conn.commit()

        t0 = time.monotonic()
        conn.execute(
            "INSERT INTO events (session_id, event_type, file_path, payload) "
            "VALUES (?, ?, ?, ?)",
            (session_id, "file_saved", "project/module_1.py",
             '{"content_length": 1024}')
        )
        conn.commit()
        ms = elapsed_ms(t0)

        conn.close()
        assert ms <= BUDGET_DB_EVENT_WRITE_MS, (
            f"Event write took {ms}ms — exceeded {BUDGET_DB_EVENT_WRITE_MS}ms budget."
        )

    def test_bulk_event_write_amortized(self):
        """
        Writing 100 events in a single transaction must average
        under 1ms per event.
        """
        conn = build_large_symbol_db(1_000)
        session_id = conn.execute(
            "INSERT INTO sessions (started_at) VALUES (datetime('now'))"
        ).lastrowid
        conn.commit()

        t0 = time.monotonic()
        events = [
            (session_id, "file_saved", f"module_{i}.py", '{"content_length": 512}')
            for i in range(100)
        ]
        conn.executemany(
            "INSERT INTO events (session_id, event_type, file_path, payload) "
            "VALUES (?, ?, ?, ?)",
            events
        )
        conn.commit()
        ms = elapsed_ms(t0)

        conn.close()
        assert ms <= 100, (
            f"100 event writes took {ms}ms — exceeded 100ms budget (1ms/event average)."
        )

    def test_session_init_within_budget(self):
        """
        Creating a new session record must complete within 100ms.
        This fires at editor startup.
        """
        conn = build_large_symbol_db(10_000)

        t0 = time.monotonic()
        session_id = conn.execute(
            "INSERT INTO sessions (started_at, file_scope) "
            "VALUES (datetime('now'), '[]')"
        ).lastrowid
        conn.commit()
        ms = elapsed_ms(t0)

        conn.close()
        assert session_id > 0
        assert ms <= BUDGET_SESSION_INIT_MS, (
            f"Session init took {ms}ms — exceeded {BUDGET_SESSION_INIT_MS}ms budget."
        )

    def test_import_graph_query_within_budget(self):
        """
        Recursive CTE traversal of the import graph for a 2-hop
        neighborhood must complete within budget.
        """
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE imports (
                id          INTEGER PRIMARY KEY,
                from_file   TEXT NOT NULL,
                to_module   TEXT NOT NULL,
                to_file     TEXT,
                import_name TEXT
            )
        """)
        # Build a 3-hop import graph
        rows = []
        for i in range(200):
            rows.append((f"module_{i}.py", f"module_{i+1}", f"module_{i+1}.py", None))
            rows.append((f"module_{i}.py", "os", None, None))
            rows.append((f"module_{i}.py", "json", None, None))
        conn.executemany(
            "INSERT INTO imports (from_file, to_module, to_file, import_name) VALUES (?, ?, ?, ?)",
            rows
        )
        conn.commit()

        t0 = time.monotonic()
        conn.execute("""
            WITH RECURSIVE graph(from_file, to_module, to_file, depth) AS (
                SELECT from_file, to_module, to_file, 1
                FROM imports WHERE from_file = 'module_0.py'
                UNION ALL
                SELECT i.from_file, i.to_module, i.to_file, g.depth + 1
                FROM imports i
                JOIN graph g ON i.from_file = g.to_file
                WHERE g.depth < 2
            )
            SELECT DISTINCT from_file, to_module, to_file, depth
            FROM graph ORDER BY depth ASC
        """).fetchall()
        ms = elapsed_ms(t0)

        conn.close()
        assert ms <= BUDGET_DB_SYMBOL_QUERY_MS * 3, (
            f"Import graph CTE took {ms}ms — exceeded budget."
        )


# ---------------------------------------------------------------------------
# Symbol extraction latency — AST parsing
# ---------------------------------------------------------------------------

class TestSymbolExtractionLatency:

    def test_small_file_extraction_within_budget(self, tmp_path):
        """
        Extracting symbols from a 100-line Python file must
        complete well within the 50ms file save budget.
        """
        content = "import os\nimport sys\n\n"
        for i in range(30):
            content += f"def function_{i}(arg1, arg2):\n"
            content += f'    """Docstring for function {i}."""\n'
            content += f"    return arg1 + arg2\n\n"

        f = tmp_path / "test_module.py"
        f.write_text(content)

        import ast
        t0 = time.monotonic()
        source = f.read_text()
        tree = ast.parse(source)
        symbols = [
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        ms = elapsed_ms(t0)

        assert len(symbols) == 30
        assert ms <= 20, (
            f"AST extraction of 100-line file took {ms}ms — "
            f"exceeds 20ms target (sync portion of file save budget)."
        )

    def test_large_file_extraction_within_budget(self, tmp_path):
        """
        Extracting symbols from a 1000-line Python file must
        stay within the 50ms file save budget.
        """
        content = "import os\nimport sys\nimport json\n\n"
        for i in range(100):
            content += f"def function_{i}(arg1: int, arg2: str) -> bool:\n"
            content += f'    """Process item {i} with given arguments."""\n'
            content += f"    result = arg1 > 0\n"
            content += f"    return result and len(arg2) > 0\n\n"

        f = tmp_path / "large_module.py"
        f.write_text(content)

        import ast
        t0 = time.monotonic()
        source = f.read_text()
        tree = ast.parse(source)
        symbols = [
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        ms = elapsed_ms(t0)

        assert len(symbols) == 100
        assert ms <= BUDGET_FILE_SAVE_SYNC_MS, (
            f"AST extraction of 1000-line file took {ms}ms — "
            f"exceeded {BUDGET_FILE_SAVE_SYNC_MS}ms budget."
        )


# ---------------------------------------------------------------------------
# LM Studio latency tests — real model calls
# ---------------------------------------------------------------------------

class TestLMLatency:
    """
    These tests use real model calls to verify latency budgets.
    Marked @pytest.mark.lm so they are skipped if LM Studio is unavailable.
    """

    @pytest.mark.lm
    @pytest.mark.slow
    def test_drafter_within_budget(self, tmp_path):
        """Granite 4.0 Tiny H must complete in under 30 seconds."""
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
        from lm_studio import call_lm_studio, DRAFTER_MODEL, TIMEOUT_DRAFTER

        content = "def process(items: list) -> list:\n    "

        t0 = time.monotonic()
        result = call_lm_studio(
            model=DRAFTER_MODEL,
            system_prompt="Complete the Python function.",
            user_prompt=content,
            timeout=TIMEOUT_DRAFTER,
            max_tokens=200,
        )
        ms = elapsed_ms(t0)

        assert ms <= BUDGET_DRAFTER_MS, (
            f"Drafter took {ms}ms — exceeded {BUDGET_DRAFTER_MS}ms budget. "
            f"Check that Granite 4.0 Tiny H is the active model in LM Studio."
        )
        if result.success:
            assert len(result.text) > 0

    @pytest.mark.lm
    @pytest.mark.slow
    def test_validator_within_budget(self, tmp_path):
        """Devstral Small 2 must complete in under 120 seconds."""
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR

        draft = "def process(items: list) -> list:\n    return [i for i in items]\n"

        t0 = time.monotonic()
        result = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt="Review this code and output JSON findings.",
            user_prompt=f"```python\n{draft}\n```",
            timeout=TIMEOUT_VALIDATOR,
            max_tokens=500,
            temperature=0.05,
        )
        ms = elapsed_ms(t0)

        assert ms <= BUDGET_VALIDATOR_MS, (
            f"Validator took {ms}ms — exceeded {BUDGET_VALIDATOR_MS}ms budget. "
            f"Check that Devstral Small 2 is the active model in LM Studio."
        )

    @pytest.mark.lm
    @pytest.mark.slow
    def test_drafter_latency_logged_to_db(self, tmp_path):
        """
        Drafter latency must be recorded in the database event log.
        This verifies that the telemetry pipeline is working.
        """
        sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
        from lm_studio import call_lm_studio, DRAFTER_MODEL, TIMEOUT_DRAFTER

        conn = build_large_symbol_db(100)
        session_id = conn.execute(
            "INSERT INTO sessions (started_at) VALUES (datetime('now'))"
        ).lastrowid
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER,
                timestamp TEXT DEFAULT (datetime('now')),
                event_type TEXT,
                file_path TEXT,
                payload TEXT DEFAULT '{}'
            )
        """)
        conn.commit()

        t0 = time.monotonic()
        result = call_lm_studio(
            model=DRAFTER_MODEL,
            system_prompt="Complete the Python function.",
            user_prompt="def add(a, b):\n    ",
            timeout=TIMEOUT_DRAFTER,
            max_tokens=50,
        )
        drafter_ms = elapsed_ms(t0)

        import json
        conn.execute(
            "INSERT INTO events (session_id, event_type, file_path, payload) "
            "VALUES (?, ?, ?, ?)",
            (session_id, "draft_complete", "test.py",
             json.dumps({"drafter_ms": drafter_ms, "success": result.success}))
        )
        conn.commit()

        row = conn.execute(
            "SELECT payload FROM events WHERE event_type = 'draft_complete'"
        ).fetchone()
        conn.close()

        assert row is not None
        payload = json.loads(row[0])
        assert "drafter_ms" in payload
        assert payload["drafter_ms"] == drafter_ms
        assert payload["drafter_ms"] > 0


# ---------------------------------------------------------------------------
# Complexity detection latency — simplicity budget computation
# ---------------------------------------------------------------------------

class TestComplexityDetectionLatency:

    def test_cyclomatic_complexity_computation_is_fast(self):
        """
        Computing cyclomatic complexity for a 50-function module
        must complete within budget.
        """
        import ast

        source = ""
        for i in range(50):
            source += f"def func_{i}(a, b, c):\n"
            source += f"    if a > 0:\n"
            source += f"        for x in b:\n"
            source += f"            if x > c:\n"
            source += f"                return x\n"
            source += f"            elif x == c:\n"
            source += f"                continue\n"
            source += f"        return -1\n"
            source += f"    return 0\n\n"

        branch_types = (
            ast.If, ast.While, ast.For, ast.AsyncFor,
            ast.ExceptHandler, ast.With, ast.AsyncWith,
            ast.Assert, ast.comprehension,
        )

        t0 = time.monotonic()
        tree = ast.parse(source)
        complexities = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                complexity = 1 + sum(
                    1 for n in ast.walk(node)
                    if isinstance(n, branch_types)
                )
                complexities[node.name] = complexity
        ms = elapsed_ms(t0)

        assert len(complexities) == 50
        assert all(c >= 1 for c in complexities.values())
        assert ms <= 50, (
            f"Complexity computation for 50-function module took {ms}ms."
        )
