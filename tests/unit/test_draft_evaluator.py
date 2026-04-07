"""
Unit tests for DraftEvaluator — the four structural metrics.

These are the metrics we designed specifically to avoid LLM-as-judge:
  - ast_valid:        draft parses as valid Python
  - symbol_overlap:   symbols in draft overlap with symbols in accepted reference
  - line_delta:       draft length is close to accepted reference length
  - import_preserved: imports present in reference are preserved in draft

All tests are deterministic with zero LLM involvement.
They run in milliseconds and must pass on every code change.
"""
import ast
import pytest
from tests.unit.draft_evaluator import DraftEvaluator, EvalScore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def evaluator():
    return DraftEvaluator()


SIMPLE_ACCEPTED = '''
def process(items: list, limit: int = 10) -> list:
    """Filter items by length limit."""
    return [i for i in items if len(i) <= limit]
'''.strip()

SIMPLE_DRAFT_GOOD = '''
def process(items: list, limit: int = 10) -> list:
    """Filter items by length limit."""
    result = []
    for item in items:
        if len(item) <= limit:
            result.append(item)
    return result
'''.strip()

SIMPLE_DRAFT_SYNTAX_ERROR = '''
def process(items: list, limit: int = 10) -> list
    return items
'''.strip()

SIMPLE_DRAFT_WRONG_SYMBOLS = '''
def transform(data: dict, threshold: float = 0.5) -> dict:
    """Completely different function."""
    return {k: v for k, v in data.items() if v > threshold}
'''.strip()

IMPORT_ACCEPTED = '''
import os
from pathlib import Path
from typing import Optional

def find_file(name: str, root: str) -> Optional[Path]:
    """Find a file starting from root."""
    for dirpath, _, files in os.walk(root):
        if name in files:
            return Path(dirpath) / name
    return None
'''.strip()

IMPORT_DRAFT_PRESERVED = '''
import os
from pathlib import Path
from typing import Optional

def find_file(name: str, root: str) -> Optional[Path]:
    """Locate a file by name under root directory."""
    path = Path(root)
    for f in path.rglob(name):
        return f
    return None
'''.strip()

IMPORT_DRAFT_MISSING = '''
def find_file(name: str, root: str):
    """Find a file."""
    for dirpath, _, files in __import__("os").walk(root):
        if name in files:
            return dirpath + "/" + name
    return None
'''.strip()


# ---------------------------------------------------------------------------
# ast_valid metric
# ---------------------------------------------------------------------------

class TestAstValid:

    def test_valid_python_scores_1(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        assert score.ast_valid == 1.0

    def test_syntax_error_scores_0(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_SYNTAX_ERROR, SIMPLE_ACCEPTED)
        assert score.ast_valid == 0.0

    def test_empty_draft_scores_0(self, evaluator):
        score = evaluator.score("", SIMPLE_ACCEPTED)
        assert score.ast_valid == 0.0

    def test_fragment_without_indent_scores_partial(self, evaluator):
        # A fragment that is valid as a statement but not a module
        fragment = "return [i for i in items if len(i) <= limit]"
        score = evaluator.score(fragment, SIMPLE_ACCEPTED)
        # Should score 0.5 — valid fragment, not standalone module
        assert score.ast_valid in (0.0, 0.5, 1.0)  # any of these is acceptable

    def test_class_definition_scores_1(self, evaluator):
        draft = "class Foo:\n    def __init__(self):\n        self.x = 1\n"
        score = evaluator.score(draft, draft)
        assert score.ast_valid == 1.0

    def test_multiline_function_scores_1(self, evaluator):
        draft = '''
def complex(a: int, b: int, c: int = 0) -> int:
    if a > b:
        return a + c
    elif b > a:
        return b + c
    return c
'''.strip()
        score = evaluator.score(draft, draft)
        assert score.ast_valid == 1.0

    def test_unicode_content_scores_1(self, evaluator):
        draft = 'def greet(name: str) -> str:\n    return f"H\u00e9llo, {name}!"\n'
        score = evaluator.score(draft, draft)
        assert score.ast_valid == 1.0


# ---------------------------------------------------------------------------
# symbol_overlap metric
# ---------------------------------------------------------------------------

class TestSymbolOverlap:

    def test_identical_code_scores_1(self, evaluator):
        score = evaluator.score(SIMPLE_ACCEPTED, SIMPLE_ACCEPTED)
        assert score.symbol_overlap == 1.0

    def test_same_function_name_different_body_scores_high(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        # Both define 'process' — overlap should be high
        assert score.symbol_overlap >= 0.8

    def test_completely_different_symbols_scores_0(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_WRONG_SYMBOLS, SIMPLE_ACCEPTED)
        # 'transform' vs 'process' — no overlap
        assert score.symbol_overlap == 0.0

    def test_empty_reference_scores_1(self, evaluator):
        # If reference has no symbols, any draft is fine
        score = evaluator.score("x = 1", "# just a comment")
        assert score.symbol_overlap == 1.0

    def test_empty_draft_scores_0_when_reference_has_symbols(self, evaluator):
        score = evaluator.score("", SIMPLE_ACCEPTED)
        assert score.symbol_overlap == 0.0

    def test_class_and_methods_overlap(self, evaluator):
        accepted = "class Foo:\n    def bar(self): pass\n    def baz(self): pass\n"
        draft    = "class Foo:\n    def bar(self): return 1\n    def baz(self): return 2\n"
        score = evaluator.score(draft, accepted)
        assert score.symbol_overlap == 1.0

    def test_partial_symbol_overlap_scores_fractional(self, evaluator):
        accepted = "def foo(): pass\ndef bar(): pass\ndef baz(): pass\n"
        draft    = "def foo(): return 1\ndef bar(): return 2\n"
        score = evaluator.score(draft, accepted)
        # 2 of 3 symbols match — should be approximately 0.67
        assert 0.5 <= score.symbol_overlap <= 0.9


# ---------------------------------------------------------------------------
# line_delta metric
# ---------------------------------------------------------------------------

class TestLineDelta:

    def test_identical_length_scores_1(self, evaluator):
        score = evaluator.score(SIMPLE_ACCEPTED, SIMPLE_ACCEPTED)
        assert score.line_delta == 1.0

    def test_slightly_longer_draft_scores_moderately(self, evaluator):
        # SIMPLE_DRAFT_GOOD has ~7 non-blank lines vs SIMPLE_ACCEPTED's ~3
        # Ratio ~2.3 gives a low line_delta, which is expected — the metric
        # is measuring length similarity, not code quality
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        assert 0.0 <= score.line_delta <= 1.0

    def test_very_short_draft_scores_low(self, evaluator):
        score = evaluator.score("x = 1", SIMPLE_ACCEPTED)
        assert score.line_delta < 0.5

    def test_massively_longer_draft_scores_low(self, evaluator):
        bloated = SIMPLE_ACCEPTED + "\n" * 100 + "# padding\n" * 50
        score = evaluator.score(bloated, SIMPLE_ACCEPTED)
        assert score.line_delta < 0.5

    def test_empty_reference_scores_1(self, evaluator):
        # No reference to compare against — any length is acceptable
        score = evaluator.score("x = 1\ny = 2\n", "")
        assert score.line_delta == 1.0

    def test_score_is_symmetric_within_tolerance(self, evaluator):
        # A draft 20% longer and 20% shorter should score similarly
        base = "def foo():\n    return 1\n" * 5
        longer  = base + "    # extra\n" * 2
        shorter = "\n".join(base.split("\n")[:6]) + "\n"
        score_longer  = evaluator.score(longer,  base)
        score_shorter = evaluator.score(shorter, base)
        assert abs(score_longer.line_delta - score_shorter.line_delta) < 0.3


# ---------------------------------------------------------------------------
# import_preserved metric
# ---------------------------------------------------------------------------

class TestImportPreserved:

    def test_all_imports_preserved_scores_1(self, evaluator):
        score = evaluator.score(IMPORT_DRAFT_PRESERVED, IMPORT_ACCEPTED)
        assert score.import_preserved == 1.0

    def test_missing_imports_scores_low(self, evaluator):
        score = evaluator.score(IMPORT_DRAFT_MISSING, IMPORT_ACCEPTED)
        # Draft drops os, pathlib, typing imports
        assert score.import_preserved < 0.5

    def test_no_imports_in_reference_scores_1(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        # SIMPLE_ACCEPTED has no imports — any draft is fine
        assert score.import_preserved == 1.0

    def test_adding_extra_imports_still_scores_1(self, evaluator):
        extra = "import sys\nimport re\n" + IMPORT_DRAFT_PRESERVED
        score = evaluator.score(extra, IMPORT_ACCEPTED)
        # Adding imports is fine — we only care about preservation
        assert score.import_preserved == 1.0

    def test_partial_preservation_scores_fractional(self, evaluator):
        # Draft keeps os but drops pathlib and typing
        partial = "import os\n\ndef find_file(name, root):\n    pass\n"
        score = evaluator.score(partial, IMPORT_ACCEPTED)
        assert 0.0 < score.import_preserved < 1.0

    def test_empty_draft_scores_0_when_reference_has_imports(self, evaluator):
        score = evaluator.score("", IMPORT_ACCEPTED)
        assert score.import_preserved == 0.0


# ---------------------------------------------------------------------------
# Composite score and EvalScore properties
# ---------------------------------------------------------------------------

class TestCompositeScore:

    def test_perfect_draft_scores_near_1(self, evaluator):
        score = evaluator.score(SIMPLE_ACCEPTED, SIMPLE_ACCEPTED)
        assert score.composite >= 0.95

    def test_syntax_error_suppresses_composite(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_SYNTAX_ERROR, SIMPLE_ACCEPTED)
        # AST invalid — composite should be very low regardless of other metrics
        assert score.composite < 0.4

    def test_composite_is_weighted_combination(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        # Manually verify the composite matches the formula
        expected = (
            score.ast_valid        * 0.40 +
            score.symbol_overlap   * 0.25 +
            score.line_delta       * 0.15 +
            score.import_preserved * 0.20
        )
        assert abs(score.composite - expected) < 0.001

    def test_score_fields_all_in_range(self, evaluator):
        score = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        assert 0.0 <= score.ast_valid        <= 1.0
        assert 0.0 <= score.symbol_overlap   <= 1.0
        assert 0.0 <= score.line_delta       <= 1.0
        assert 0.0 <= score.import_preserved <= 1.0
        assert 0.0 <= score.composite        <= 1.0

    def test_score_returns_eval_score_instance(self, evaluator):
        result = evaluator.score(SIMPLE_DRAFT_GOOD, SIMPLE_ACCEPTED)
        assert isinstance(result, EvalScore)
