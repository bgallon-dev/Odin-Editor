"""
Unit tests for OdinStructuralGate — the four structural metrics for Odin drafts.

Metrics:
  - braces_balanced:     simulated file has balanced { }
  - no_escaped_newlines: no \\n / \\t artifacts outside string literals
  - symbol_overlap:      draft identifiers appear in file or known symbols
  - import_preserved:    file imports survive after draft insertion

All tests are deterministic with zero LLM involvement.
They run in milliseconds and must pass on every code change.
"""
import pytest
from odin_structural_gate import OdinStructuralGate, StructuralScore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gate():
    return OdinStructuralGate()


ODIN_FILE = '''package editor

import rl "vendor:raylib"
import "core:fmt"

Editor_State :: struct {
    tab_bar: Tab_Bar,
    font:    rl.Font,
}

init_editor :: proc() -> Editor_State {
    fmt.println("init")
    return Editor_State{}
}
'''.strip()


# ---------------------------------------------------------------------------
# TestBracesBalanced
# ---------------------------------------------------------------------------

class TestBracesBalanced:

    def test_balanced_code(self, gate):
        source = 'main :: proc() { x := 1 }'
        assert gate._braces_balanced(source) == 1.0

    def test_missing_closing_brace(self, gate):
        source = 'main :: proc() { x := 1'
        assert gate._braces_balanced(source) == 0.0

    def test_extra_closing_brace(self, gate):
        source = 'main :: proc() { x := 1 } }'
        assert gate._braces_balanced(source) == 0.0

    def test_nested_braces(self, gate):
        source = 'main :: proc() { if true { x := 1 } }'
        assert gate._braces_balanced(source) == 1.0

    def test_braces_in_string_ignored(self, gate):
        source = 'main :: proc() { s := "{ not a brace }" }'
        assert gate._braces_balanced(source) == 1.0

    def test_braces_in_line_comment_ignored(self, gate):
        source = 'main :: proc() { // { unclosed\n}'
        assert gate._braces_balanced(source) == 1.0

    def test_braces_in_block_comment_ignored(self, gate):
        source = 'main :: proc() { /* { */ }'
        assert gate._braces_balanced(source) == 1.0

    def test_nested_block_comments(self, gate):
        source = 'main :: proc() { /* /* { */ */ }'
        assert gate._braces_balanced(source) == 1.0

    def test_empty_string(self, gate):
        assert gate._braces_balanced('') == 1.0

    def test_backtick_string_ignored(self, gate):
        source = 'main :: proc() { s := `{ raw }` }'
        assert gate._braces_balanced(source) == 1.0


# ---------------------------------------------------------------------------
# TestNoEscapedNewlines
# ---------------------------------------------------------------------------

class TestNoEscapedNewlines:

    def test_clean_code(self, gate):
        draft = 'x := 1\ny := 2\n'
        assert gate._no_escaped_newlines(draft) == 1.0

    def test_escaped_newline_artifact(self, gate):
        # Literal backslash-n outside a string
        draft = 'x := 1\\ny := 2'
        assert gate._no_escaped_newlines(draft) == 0.0

    def test_escaped_tab_artifact(self, gate):
        draft = 'x := 1\\ty := 2'
        assert gate._no_escaped_newlines(draft) == 0.0

    def test_escaped_newline_inside_string_ok(self, gate):
        draft = 'msg := "hello\\nworld"'
        assert gate._no_escaped_newlines(draft) == 1.0

    def test_escaped_quote_artifact(self, gate):
        draft = 'x := 1\\"extra'
        assert gate._no_escaped_newlines(draft) == 0.0

    def test_backslash_backslash_artifact(self, gate):
        draft = 'path := C:\\\\Users'
        assert gate._no_escaped_newlines(draft) == 0.0

    def test_empty_draft(self, gate):
        assert gate._no_escaped_newlines('') == 1.0

    def test_backtick_string_escaped_ok(self, gate):
        # Raw strings in backticks — \n inside is literal text, not an artifact
        # But our check looks for backslash-n outside strings, and backticks
        # count as strings, so this should be fine
        draft = 's := `hello\\nworld`'
        assert gate._no_escaped_newlines(draft) == 1.0


# ---------------------------------------------------------------------------
# TestSymbolOverlap
# ---------------------------------------------------------------------------

class TestSymbolOverlap:

    def test_all_known_symbols(self, gate):
        draft = 'Editor_State Tab_Bar'
        file_content = 'Editor_State :: struct { tab_bar: Tab_Bar }'
        score = gate._symbol_overlap(draft, file_content, set())
        assert score == 1.0

    def test_all_unknown_symbols(self, gate):
        draft = 'FooBar BazQux'
        file_content = 'Editor_State :: struct {}'
        score = gate._symbol_overlap(draft, file_content, set())
        assert score == 0.0

    def test_partial_overlap(self, gate):
        draft = 'Editor_State UnknownThing'
        file_content = 'Editor_State :: struct {}'
        score = gate._symbol_overlap(draft, file_content, set())
        assert score == 0.5

    def test_keywords_filtered(self, gate):
        # Only keywords — all filtered out, so score is 1.0
        draft = 'proc struct if else return'
        file_content = 'something :: proc() {}'
        score = gate._symbol_overlap(draft, file_content, set())
        assert score == 1.0

    def test_known_symbols_from_db(self, gate):
        draft = 'Editor_State init_editor'
        file_content = ''  # empty file content
        known = {'Editor_State', 'init_editor'}
        score = gate._symbol_overlap(draft, file_content, known)
        assert score == 1.0

    def test_empty_draft(self, gate):
        score = gate._symbol_overlap('', 'some content', set())
        assert score == 1.0

    def test_mixed_known_and_db(self, gate):
        draft = 'Editor_State Tab_Bar init_editor'
        file_content = 'Editor_State :: struct { tab_bar: Tab_Bar }'
        known = {'init_editor'}
        score = gate._symbol_overlap(draft, file_content, known)
        assert score == 1.0


# ---------------------------------------------------------------------------
# TestImportPreserved
# ---------------------------------------------------------------------------

class TestImportPreserved:

    def test_all_imports_preserved(self, gate):
        original = 'import rl "vendor:raylib"\nimport "core:fmt"\n\nmain :: proc() {}'
        simulated = 'import rl "vendor:raylib"\nimport "core:fmt"\n\nmain :: proc() { x := 1 }'
        assert gate._import_preserved(simulated, original) == 1.0

    def test_import_removed(self, gate):
        original = 'import rl "vendor:raylib"\nimport "core:fmt"\n\nmain :: proc() {}'
        # Simulated file lost the fmt import
        simulated = 'import rl "vendor:raylib"\n\nmain :: proc() { x := 1 }'
        assert gate._import_preserved(simulated, original) == 0.5

    def test_no_imports_in_original(self, gate):
        original = 'main :: proc() {}'
        simulated = 'main :: proc() { x := 1 }'
        assert gate._import_preserved(simulated, original) == 1.0

    def test_foreign_import_preserved(self, gate):
        original = 'foreign import kernel32 "system:Kernel32.lib"\nimport "core:fmt"'
        simulated = 'foreign import kernel32 "system:Kernel32.lib"\nimport "core:fmt"\nx := 1'
        assert gate._import_preserved(simulated, original) == 1.0

    def test_aliased_import(self, gate):
        original = 'import win32 "core:sys/windows"'
        simulated = 'import win32 "core:sys/windows"\nx := 1'
        assert gate._import_preserved(simulated, original) == 1.0

    def test_all_imports_removed(self, gate):
        original = 'import "core:fmt"\nimport "core:os"'
        simulated = 'x := 1'
        assert gate._import_preserved(simulated, original) == 0.0


# ---------------------------------------------------------------------------
# TestCompositeAndHardReject
# ---------------------------------------------------------------------------

class TestCompositeAndHardReject:

    def test_perfect_score(self, gate):
        # Draft that preserves everything
        draft = 'fmt.println("hello")'
        result = gate.score(draft, ODIN_FILE, len(ODIN_FILE), {'fmt'})
        assert result.braces_balanced == 1.0
        assert result.hard_reject is False
        assert 0.0 <= result.composite <= 1.0

    def test_hard_reject_on_unbalanced(self, gate):
        # Draft that breaks brace balance
        draft = '{ unclosed'
        result = gate.score(draft, ODIN_FILE, len(ODIN_FILE))
        assert result.braces_balanced == 0.0
        assert result.hard_reject is True

    def test_composite_is_weighted_sum(self, gate):
        draft = 'fmt.println("hello")'
        result = gate.score(draft, ODIN_FILE, len(ODIN_FILE), {'fmt'})
        expected = (
            result.braces_balanced     * StructuralScore.WEIGHTS["braces_balanced"]
            + result.no_escaped_newlines * StructuralScore.WEIGHTS["no_escaped_newlines"]
            + result.symbol_overlap    * StructuralScore.WEIGHTS["symbol_overlap"]
            + result.import_preserved  * StructuralScore.WEIGHTS["import_preserved"]
        )
        assert abs(result.composite - round(expected, 6)) < 1e-6

    def test_weights_sum_to_one(self):
        total = sum(StructuralScore.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_all_fields_in_range(self, gate):
        draft = 'Editor_State Tab_Bar'
        result = gate.score(draft, ODIN_FILE, 50)
        for field in ['braces_balanced', 'no_escaped_newlines',
                      'symbol_overlap', 'import_preserved', 'composite']:
            val = getattr(result, field)
            assert 0.0 <= val <= 1.0, f"{field} = {val} out of range"

    def test_cursor_at_start(self, gate):
        draft = '// comment\n'
        result = gate.score(draft, ODIN_FILE, 0)
        assert result.braces_balanced == 1.0
        assert result.import_preserved == 1.0

    def test_cursor_in_middle(self, gate):
        # Insert inside the struct — should still be balanced
        # Find offset after "struct {"
        offset = ODIN_FILE.index('struct {') + len('struct {')
        draft = '\n    width: f32,'
        result = gate.score(draft, ODIN_FILE, offset)
        assert result.braces_balanced == 1.0
        assert result.import_preserved == 1.0
