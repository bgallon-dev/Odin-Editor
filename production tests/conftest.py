"""
conftest.py — shared fixtures and configuration for the full test suite.

Test layers:
  tests/unit/        — deterministic, zero LLM, milliseconds each
  tests/integration/ — real databases and real LLM calls, seconds to minutes
  tests/evals/       — quality scoring against fixed corpus, minutes

Markers:
  @pytest.mark.lm    — requires LM Studio running with both models loaded
  @pytest.mark.slow  — takes more than 10 seconds
  @pytest.mark.eval  — scores quality, does not hard-fail on LLM output content
"""
import sys
import pathlib
import sqlite3
import pytest

# Make all source modules importable
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Marker registration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "lm: test requires LM Studio running with Granite and Devstral loaded"
    )
    config.addinivalue_line(
        "markers",
        "slow: test takes more than 10 seconds"
    )
    config.addinivalue_line(
        "markers",
        "eval: evaluation test — records quality scores, soft failure threshold"
    )


def pytest_runtest_setup(item):
    """Auto-skip LM tests if LM Studio is not reachable."""
    if "lm" in item.keywords:
        try:
            from lm_studio import check_lm_studio_available
            if not check_lm_studio_available():
                pytest.skip(
                    "LM Studio is not running. "
                    "Start it and load both Granite 4.0 Tiny H and Devstral Small 2."
                )
        except ImportError:
            pytest.skip("lm_studio module not found")


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_db():
    """Fresh in-memory project database with minimal schema."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS system_config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at  TEXT    NOT NULL DEFAULT (datetime('now')),
            closed_at   TEXT,
            file_scope  TEXT    DEFAULT '[]',
            event_count INTEGER NOT NULL DEFAULT 0,
            summary     TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES sessions(id),
            timestamp  REAL    NOT NULL DEFAULT (unixepoch('now', 'subsec')),
            event_type TEXT    NOT NULL,
            file_path  TEXT,
            payload    TEXT    DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS symbols (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path     TEXT    NOT NULL,
            name          TEXT    NOT NULL,
            kind          TEXT    NOT NULL,
            signature     TEXT    DEFAULT '',
            docstring     TEXT    DEFAULT '',
            line_start    INTEGER NOT NULL DEFAULT 0,
            line_end      INTEGER NOT NULL DEFAULT 0,
            last_seen     TEXT    NOT NULL DEFAULT (datetime('now')),
            session_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE(file_path, name, kind)
        );
    """)
    conn.execute(
        "INSERT INTO system_config VALUES ('schema_version', '1')"
    )
    conn.execute(
        "INSERT INTO system_config VALUES ('seeding_complete', 'true')"
    )
    conn.commit()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# File fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_python_file(tmp_path):
    """Complete Python file — used for accept/dismiss tests."""
    content = '''"""Sample module for testing the Kettle pipeline."""
import os
from typing import Optional, List


def process_items(items: List[str], limit: int = 10) -> List[str]:
    """Filter and process a list of items by length."""
    results = []
    for item in items:
        if len(item) <= limit:
            results.append(item.strip())
    return results


class DataProcessor:
    """Processes data from multiple sources."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.processed = 0

    def run(self, data: List[str]) -> Optional[List[str]]:
        """Run the processor against data."""
        if not data:
            return None
        result = process_items(data, self.config.get("limit", 10))
        self.processed += len(result)
        return result
'''
    path = tmp_path / "sample.py"
    path.write_text(content, encoding="utf-8")
    return path, content


@pytest.fixture
def incomplete_python_file(tmp_path):
    """Python file with an incomplete function — primary drafter scenario."""
    content = '''"""Module with incomplete code."""
from typing import List, Dict, Optional
import json


def extract_metrics(data: List[Dict]) -> Dict[str, float]:
    """Extract summary metrics from a list of records.
    
    Returns a dict with keys: mean, min, max, count.
    Returns empty dict if data is empty.
    """
'''
    path = tmp_path / "incomplete.py"
    path.write_text(content, encoding="utf-8")
    return path, content


@pytest.fixture
def over_complex_python_file(tmp_path):
    """Deliberately over-complex Python file for complexity tests."""
    content = '''"""Module with complexity violations."""


def process_all(raw_data, config, validator, transformer,
                logger, cache, metrics, retry):
    """God function doing everything at once."""
    results = []
    errors = []
    for item in raw_data:
        try:
            if config.get("validate"):
                if not validator.check(item):
                    if config.get("strict"):
                        if logger:
                            logger.error(item)
                        errors.append(item)
                        continue
                    else:
                        continue
            t = transformer.apply(item)
            if t is None:
                for attempt in range(retry.max):
                    t = transformer.apply(item)
                    if t:
                        break
                    if attempt == retry.max - 1:
                        errors.append(item)
                        continue
            if cache.has(t):
                results.append(cache.get(t))
                metrics.record("hit")
                continue
            final = transformer.finalize(t)
            if final:
                results.append(final)
                cache.put(t, final)
                metrics.record("ok")
            else:
                errors.append(item)
                metrics.record("fail")
        except Exception as e:
            if logger:
                logger.exception(e)
            errors.append(item)
    return results, errors
'''
    path = tmp_path / "complex.py"
    path.write_text(content, encoding="utf-8")
    return path, content
