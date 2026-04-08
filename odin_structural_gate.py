"""
OdinStructuralGate — fast, deterministic structural checks for Odin drafts.

Runs in the pipeline between sanitization and the validator LLM call.
Four metrics, all millisecond-scale, no LLM, no compilation:
  1. Brace balance (simulated full file)
  2. Escaped-newline artifact detection
  3. Symbol overlap against known identifiers
  4. Import preservation after insertion
"""
import re
from dataclasses import dataclass


def _debug(msg: str):
    print(f"[DEBUG][structural_gate] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Odin keywords and builtins — single source of truth
# ---------------------------------------------------------------------------
ODIN_KEYWORDS_AND_BUILTINS: frozenset[str] = frozenset({
    # Keywords
    'proc', 'struct', 'enum', 'union', 'for', 'if', 'else', 'when',
    'return', 'do', 'break', 'continue', 'switch', 'case', 'defer',
    'using', 'import', 'package', 'foreign', 'where', 'distinct',
    'dynamic', 'map', 'bit_set', 'matrix', 'or_else', 'or_return',
    'true', 'false', 'nil',
    # Numeric types
    'int', 'uint', 'i8', 'i16', 'i32', 'i64', 'i128',
    'u8', 'u16', 'u32', 'u64', 'u128',
    'f16', 'f32', 'f64', 'bool', 'string', 'cstring', 'rawptr',
    'byte', 'rune', 'uintptr', 'typeid', 'any',
    # Builtins
    'len', 'cap', 'append', 'delete', 'make', 'new', 'free',
    'size_of', 'align_of', 'offset_of', 'type_of', 'type_info_of',
    'transmute', 'cast', 'auto_cast',
    # Common variable names
    'i', 'j', 'k', 'v', 'ok', 'err', 'it', 'idx',
    'context', 'allocator', 'temp_allocator',
})


@dataclass
class StructuralScore:
    braces_balanced:     float   # 1.0 balanced, 0.0 unbalanced
    no_escaped_newlines: float   # 1.0 clean, 0.0 has artifacts outside strings
    symbol_overlap:      float   # fraction of draft identifiers that are known
    import_preserved:    float   # fraction of file imports preserved post-insertion
    composite:           float   # weighted combination
    hard_reject:         bool    # True if braces_balanced == 0.0

    WEIGHTS = {
        "braces_balanced":     0.40,
        "no_escaped_newlines": 0.10,
        "symbol_overlap":      0.25,
        "import_preserved":    0.25,
    }


# Regex for Odin import statements:
#   import "core:fmt"
#   import rl "vendor:raylib"
#   foreign import kernel32 "system:Kernel32.lib"
_IMPORT_RE = re.compile(
    r'^\s*(?:foreign\s+)?import\s+(?:\w+\s+)?"([^"]+)"',
    re.MULTILINE,
)


class OdinStructuralGate:
    """
    Fast structural checks on Odin drafts before the validator LLM runs.

    All metrics are deterministic and run in milliseconds.
    """

    def score(
        self,
        draft: str,
        file_content: str,
        cursor_offset: int,
        known_symbols: set[str] | None = None,
    ) -> StructuralScore:
        _debug(f"score START: draft={len(draft)} chars, file={len(file_content)} chars, "
               f"cursor_offset={cursor_offset}, known_symbols={len(known_symbols or set())}")

        simulated = (
            file_content[:cursor_offset] + draft + file_content[cursor_offset:]
        )
        _debug(f"  simulated file: {len(simulated)} chars")

        braces      = self._braces_balanced(simulated)
        _debug(f"  metric 1 — braces_balanced: {braces}")

        escaped     = self._no_escaped_newlines(draft)
        _debug(f"  metric 2 — no_escaped_newlines: {escaped}")

        sym_overlap = self._symbol_overlap(draft, file_content, known_symbols or set())
        _debug(f"  metric 3 — symbol_overlap: {sym_overlap:.4f}")

        imports     = self._import_preserved(simulated, file_content)
        _debug(f"  metric 4 — import_preserved: {imports:.4f}")

        composite = (
            braces      * StructuralScore.WEIGHTS["braces_balanced"]
            + escaped   * StructuralScore.WEIGHTS["no_escaped_newlines"]
            + sym_overlap * StructuralScore.WEIGHTS["symbol_overlap"]
            + imports   * StructuralScore.WEIGHTS["import_preserved"]
        )

        hard_reject = (braces == 0.0)
        _debug(f"  composite={composite:.6f} hard_reject={hard_reject}")
        _debug(f"  weights: braces={StructuralScore.WEIGHTS['braces_balanced']} "
               f"escaped={StructuralScore.WEIGHTS['no_escaped_newlines']} "
               f"sym={StructuralScore.WEIGHTS['symbol_overlap']} "
               f"imports={StructuralScore.WEIGHTS['import_preserved']}")

        return StructuralScore(
            braces_balanced=braces,
            no_escaped_newlines=escaped,
            symbol_overlap=sym_overlap,
            import_preserved=imports,
            composite=round(composite, 6),
            hard_reject=hard_reject,
        )

    # ------------------------------------------------------------------
    # Metric 1: Brace balance
    # ------------------------------------------------------------------

    def _braces_balanced(self, source: str) -> float:
        """1.0 if { and } are balanced (ignoring strings/comments), else 0.0."""
        depth = 0
        i = 0
        n = len(source)

        while i < n:
            ch = source[i]

            # Line comment
            if ch == '/' and i + 1 < n and source[i + 1] == '/':
                i += 2
                while i < n and source[i] != '\n':
                    i += 1
                continue

            # Block comment (Odin supports nesting)
            if ch == '/' and i + 1 < n and source[i + 1] == '*':
                i += 2
                nest = 1
                while i < n and nest > 0:
                    if source[i] == '/' and i + 1 < n and source[i + 1] == '*':
                        nest += 1
                        i += 2
                    elif source[i] == '*' and i + 1 < n and source[i + 1] == '/':
                        nest -= 1
                        i += 2
                    else:
                        i += 1
                continue

            # Double-quoted string
            if ch == '"':
                i += 1
                while i < n and source[i] != '"':
                    if source[i] == '\\':
                        i += 1  # skip escaped char
                    i += 1
                i += 1  # skip closing quote
                continue

            # Backtick raw string
            if ch == '`':
                i += 1
                while i < n and source[i] != '`':
                    i += 1
                i += 1
                continue

            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth < 0:
                    return 0.0

            i += 1

        return 1.0 if depth == 0 else 0.0

    # ------------------------------------------------------------------
    # Metric 2: Escaped-newline artifacts
    # ------------------------------------------------------------------

    def _no_escaped_newlines(self, draft: str) -> float:
        """1.0 if no escaped-char artifacts outside strings, else 0.0."""
        i = 0
        n = len(draft)

        while i < n:
            ch = draft[i]

            # Skip double-quoted strings (where \n is legitimate)
            if ch == '"':
                i += 1
                while i < n and draft[i] != '"':
                    if draft[i] == '\\':
                        i += 1
                    i += 1
                i += 1
                continue

            # Skip backtick raw strings
            if ch == '`':
                i += 1
                while i < n and draft[i] != '`':
                    i += 1
                i += 1
                continue

            # Outside strings: check for backslash-letter artifacts
            if ch == '\\' and i + 1 < n and draft[i + 1] in ('n', 't', '"', '\\'):
                return 0.0

            i += 1

        return 1.0

    # ------------------------------------------------------------------
    # Metric 3: Symbol overlap
    # ------------------------------------------------------------------

    def _symbol_overlap(
        self,
        draft: str,
        file_content: str,
        known_symbols: set[str],
    ) -> float:
        """Fraction of draft identifiers that appear in file or known symbols."""
        draft_tokens = set(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', draft))
        draft_tokens -= ODIN_KEYWORDS_AND_BUILTINS

        if not draft_tokens:
            _debug("    symbol_overlap: no non-keyword tokens in draft → 1.0")
            return 1.0

        file_tokens = set(re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', file_content))
        reference = file_tokens | known_symbols

        overlap = draft_tokens & reference
        unknown = draft_tokens - reference
        _debug(f"    symbol_overlap: {len(draft_tokens)} draft tokens, "
               f"{len(overlap)} matched, {len(unknown)} unknown")
        if unknown:
            _debug(f"    unknown tokens: {sorted(unknown)[:15]}")
        return len(overlap) / len(draft_tokens)

    # ------------------------------------------------------------------
    # Metric 4: Import preservation
    # ------------------------------------------------------------------

    def _import_preserved(self, simulated_file: str, original_file: str) -> float:
        """Fraction of original imports still present after draft insertion."""
        original_imports = set(_IMPORT_RE.findall(original_file))
        if not original_imports:
            _debug("    import_preserved: no imports in original file → 1.0")
            return 1.0

        simulated_imports = set(_IMPORT_RE.findall(simulated_file))
        preserved = original_imports & simulated_imports
        lost = original_imports - simulated_imports
        _debug(f"    import_preserved: {len(original_imports)} original, "
               f"{len(preserved)} preserved, {len(lost)} lost")
        if lost:
            _debug(f"    lost imports: {sorted(lost)}")
        return len(preserved) / len(original_imports)
