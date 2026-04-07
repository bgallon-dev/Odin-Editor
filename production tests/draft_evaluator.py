"""
DraftEvaluator — structural quality scoring for LLM-generated drafts.

Uses four AST-based metrics that are deterministic and LLM-free.
This is the grounding layer that prevents the feedback loop from
optimizing for LLM vibes rather than structural correctness.
"""
import ast
from dataclasses import dataclass
from typing import Optional


@dataclass
class EvalScore:
    ast_valid:        float   # 1.0 valid module, 0.5 valid fragment, 0.0 invalid
    symbol_overlap:   float   # fraction of reference symbols present in draft
    line_delta:       float   # 1.0 = same length, degrades as ratio diverges
    import_preserved: float   # fraction of reference imports preserved in draft
    composite:        float   # weighted combination of the four metrics

    # Weights — sum to 1.0
    # AST validity is weighted highest because a non-parsing draft is useless.
    # Symbol overlap is next because correct symbol names are load-bearing.
    # Import preservation matters because missing imports break at runtime.
    # Line delta is the softest signal — different implementations vary legitimately.
    WEIGHTS = {
        "ast_valid":        0.40,
        "symbol_overlap":   0.25,
        "line_delta":       0.15,
        "import_preserved": 0.20,
    }


class DraftEvaluator:
    """
    Scores a generated draft against an accepted reference using
    four structural metrics derived from the AST.

    No LLM involvement. No external dependencies beyond the stdlib.
    All metrics are deterministic given the same inputs.
    """

    def score(self, draft: str, reference: str) -> EvalScore:
        ast_valid        = self._ast_valid(draft)
        symbol_overlap   = self._symbol_overlap(draft, reference)
        line_delta       = self._line_delta(draft, reference)
        import_preserved = self._import_preserved(draft, reference)

        composite = (
            ast_valid        * EvalScore.WEIGHTS["ast_valid"]        +
            symbol_overlap   * EvalScore.WEIGHTS["symbol_overlap"]   +
            line_delta       * EvalScore.WEIGHTS["line_delta"]       +
            import_preserved * EvalScore.WEIGHTS["import_preserved"]
        )

        return EvalScore(
            ast_valid=ast_valid,
            symbol_overlap=symbol_overlap,
            line_delta=line_delta,
            import_preserved=import_preserved,
            composite=round(composite, 6),
        )

    # ------------------------------------------------------------------
    # Metric implementations
    # ------------------------------------------------------------------

    def _ast_valid(self, draft: str) -> float:
        """
        1.0 — parses as a complete Python module
        0.5 — parses as a valid statement/expression fragment
        0.0 — does not parse at all
        """
        if not draft.strip():
            return 0.0
        try:
            ast.parse(draft)
            return 1.0
        except SyntaxError:
            pass
        # Try as an indented fragment inside a wrapper function
        try:
            wrapped = "def _wrapper():\n" + "\n".join(
                f"    {line}" for line in draft.split("\n")
            )
            ast.parse(wrapped)
            return 0.5
        except SyntaxError:
            return 0.0

    def _symbol_overlap(self, draft: str, reference: str) -> float:
        """
        Fraction of symbols defined in reference that are also
        defined in draft. A symbol is a function, class, or
        async function name at any nesting level.

        Returns 1.0 if reference has no symbols (nothing to violate).
        Returns 0.0 if draft has no symbols but reference does.
        """
        ref_symbols   = self._extract_symbol_names(reference)
        draft_symbols = self._extract_symbol_names(draft)

        if not ref_symbols:
            return 1.0
        if not draft_symbols:
            return 0.0

        overlap = ref_symbols & draft_symbols
        return len(overlap) / len(ref_symbols)

    def _line_delta(self, draft: str, reference: str) -> float:
        """
        Measures how close the draft length is to the reference length.
        Score of 1.0 when lengths are equal, degrades as ratio diverges.
        Returns 1.0 if reference is empty (no target to miss).

        Formula: 1.0 - min(|ratio - 1|, 1.0)
        where ratio = draft_lines / reference_lines
        """
        ref_lines   = len([l for l in reference.split("\n") if l.strip()])
        draft_lines = len([l for l in draft.split("\n") if l.strip()])

        if ref_lines == 0:
            return 1.0

        ratio = draft_lines / ref_lines
        return round(max(0.0, 1.0 - min(abs(ratio - 1.0), 1.0)), 6)

    def _import_preserved(self, draft: str, reference: str) -> float:
        """
        Fraction of modules imported in reference that are also
        imported in draft.

        Returns 1.0 if reference has no imports (nothing to violate).
        Returns 0.0 if draft has no imports but reference does.
        """
        ref_imports   = self._extract_imports(reference)
        draft_imports = self._extract_imports(draft)

        if not ref_imports:
            return 1.0
        if not draft_imports:
            return 0.0

        preserved = ref_imports & draft_imports
        return len(preserved) / len(ref_imports)

    # ------------------------------------------------------------------
    # AST extraction helpers
    # ------------------------------------------------------------------

    def _extract_symbol_names(self, source: str) -> set[str]:
        """Extract all function and class names from source."""
        if not source.strip():
            return set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            try:
                wrapped = "def _wrapper():\n" + "\n".join(
                    f"    {line}" for line in source.split("\n")
                )
                tree = ast.parse(wrapped)
            except SyntaxError:
                return set()

        names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if node.name != "_wrapper":
                    names.add(node.name)
        return names

    def _extract_imports(self, source: str) -> set[str]:
        """Extract all imported module names from source."""
        if not source.strip():
            return set()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return set()

        modules = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    # Top-level module name only: "os.path" → "os"
                    modules.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules.add(node.module.split(".")[0])
        return modules
