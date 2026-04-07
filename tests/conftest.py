import pytest
import sqlite3
import tempfile
import pathlib
import sys
import os

# Make the source modules importable from tests
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from lm_studio import check_lm_studio_available


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "lm: test requires LM Studio to be running with models loaded"
    )
    config.addinivalue_line(
        "markers",
        "slow: test takes more than 10 seconds"
    )
    config.addinivalue_line(
        "markers",
        "eval: evaluation test — scores quality, does not assert pass/fail"
    )


def pytest_runtest_setup(item):
    """Skip LM tests if LM Studio is not available."""
    if "lm" in item.keywords:
        if not check_lm_studio_available():
            pytest.skip("LM Studio not running — start it and load both models")


@pytest.fixture
def project_db():
    """Fresh in-memory project database for each test."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # Apply the project schema
    from kettle_server import PROJECT_SCHEMA
    conn.executescript(PROJECT_SCHEMA)

    # Insert required config rows
    conn.execute("INSERT INTO system_config VALUES ('schema_version', '1')")
    conn.execute("INSERT INTO system_config VALUES ('seeding_complete', 'true')")
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def sample_python_file(tmp_path):
    """A real Python file on disk that tests can use."""
    content = '''"""Sample module for testing."""
import os
from typing import Optional, List


def process_items(items: List[str], limit: int = 10) -> List[str]:
    """Filter and process a list of items."""
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
    path.write_text(content)
    return path, content


@pytest.fixture
def incomplete_python_file(tmp_path):
    """A Python file with an incomplete function — prime drafter target."""
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
