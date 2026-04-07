"""File with many imports — tests that the drafter respects import context."""
import ast
import json
import os
import pathlib
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple


@dataclass
class PipelineConfig:
    project_root: str
    db_path: str
    model_url: str = "http://127.0.0.1:1234"
    timeout: int = 30
    max_context_chars: int = 24_000


def build_context_payload(
    config: PipelineConfig,
    file_path: str,
    cursor_offset: int,
) -> str:
    """Build the full context payload for the drafter model.

    Reads the file, assembles symbols from the database,
    and returns a formatted prompt string.
    """
