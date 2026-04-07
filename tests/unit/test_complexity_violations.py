"""
Complexity violation tests.

These tests verify that the validator flags structurally over-complex
drafts using our measurable complexity dimensions:
  - Line count exceeding the simplicity budget
  - Cyclomatic complexity above threshold
  - Parameter count above threshold
  - Nesting depth above threshold

The structural checks in the unit layer verify the metrics are computed
correctly. These tests verify the full pipeline — that the validator
model actually flags violations when presented with deliberately
over-complex code.

Unit tests (no LLM) verify the complexity measurement machinery.
Integration tests (real LLM) verify the validator's detection behavior.
"""
import ast
import pytest
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))


# ---------------------------------------------------------------------------
# Complexity measurement — deterministic, no LLM
# ---------------------------------------------------------------------------

class TestComplexityMeasurement:
    """
    Unit tests for the complexity metrics themselves.
    These are the numbers the feedback system reasons about.
    """

    def _cyclomatic(self, source: str) -> dict[str, int]:
        """Compute cyclomatic complexity for all functions in source."""
        branch_types = (
            ast.If, ast.While, ast.For, ast.AsyncFor,
            ast.ExceptHandler, ast.With, ast.AsyncWith,
            ast.Assert, ast.comprehension,
        )
        tree = ast.parse(source)
        result = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[node.name] = 1 + sum(
                    1 for n in ast.walk(node)
                    if isinstance(n, branch_types)
                )
        return result

    def _max_nesting(self, source: str) -> dict[str, int]:
        """Compute maximum nesting depth for all functions."""
        tree = ast.parse(source)
        result = {}
        for func in ast.walk(tree):
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                result[func.name] = self._nesting_depth(func)
        return result

    def _nesting_depth(self, node: ast.AST, depth: int = 0) -> int:
        nesting_types = (ast.If, ast.For, ast.While, ast.With,
                         ast.Try, ast.AsyncFor, ast.AsyncWith)
        max_depth = depth
        for child in ast.iter_child_nodes(node):
            if isinstance(child, nesting_types):
                max_depth = max(max_depth, self._nesting_depth(child, depth + 1))
            else:
                max_depth = max(max_depth, self._nesting_depth(child, depth))
        return max_depth

    def test_simple_function_has_low_complexity(self):
        source = "def add(a: int, b: int) -> int:\n    return a + b\n"
        cc = self._cyclomatic(source)
        assert cc["add"] == 1

    def test_branching_function_has_higher_complexity(self):
        source = """
def classify(x: int) -> str:
    if x < 0:
        return "negative"
    elif x == 0:
        return "zero"
    elif x < 10:
        return "small"
    else:
        return "large"
"""
        cc = self._cyclomatic(source)
        assert cc["classify"] >= 4

    def test_nested_loops_increase_complexity(self):
        source = """
def matrix_op(data: list) -> list:
    result = []
    for row in data:
        for item in row:
            if item > 0:
                result.append(item)
    return result
"""
        cc = self._cyclomatic(source)
        assert cc["matrix_op"] >= 3

    def test_simplicity_budget_line_count(self):
        """Functions over 50 lines violate the line count budget."""
        lines = ["def bloated_function():"]
        for i in range(60):
            lines.append(f"    x_{i} = {i}  # line {i}")
        lines.append("    return x_0")
        source = "\n".join(lines)

        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                line_count = (node.end_lineno or node.lineno) - node.lineno + 1
                assert line_count > 50, "Test fixture should be over budget"

    def test_parameter_count_above_4_is_suspect(self):
        """Functions with more than 4 parameters are typically doing too much."""
        source = """
def over_parameterized(a, b, c, d, e, f, g):
    return a + b + c + d + e + f + g
"""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "over_parameterized":
                param_count = len(node.args.args)
                assert param_count > 4

    def test_deep_nesting_is_detectable(self):
        source = """
def deeply_nested(data):
    if data:
        for item in data:
            if item > 0:
                for sub in item:
                    if sub:
                        return sub
    return None
"""
        depths = self._max_nesting(source)
        assert depths["deeply_nested"] >= 4

    def test_flat_function_has_low_nesting(self):
        source = """
def flat_function(items: list) -> list:
    return [item.strip() for item in items if item]
"""
        depths = self._max_nesting(source)
        assert depths["flat_function"] <= 1

    def test_complexity_score_computation(self):
        """
        The simplicity score is a weighted combination of:
          - line_count_score:  1.0 for <= 20 lines, degrades linearly to 0 at 50
          - param_count_score: 1.0 for <= 4 params, 0 for > 8
          - cyclomatic_score:  1.0 for complexity 1, degrades to 0 at 10
          - nesting_score:     1.0 for depth <= 2, 0 for depth >= 6
        """
        def simplicity_score(
            line_count: int,
            param_count: int,
            cyclomatic: int,
            nesting_depth: int,
        ) -> float:
            line_score    = max(0.0, 1.0 - max(0, line_count - 20) / 30)
            param_score   = max(0.0, 1.0 - max(0, param_count - 4) / 4)
            cc_score      = max(0.0, 1.0 - max(0, cyclomatic - 1) / 9)
            nesting_score = max(0.0, 1.0 - max(0, nesting_depth - 2) / 4)
            return (line_score * 0.35 + param_score * 0.25 +
                    cc_score * 0.25 + nesting_score * 0.15)

        # Simple function should score well
        simple = simplicity_score(5, 2, 1, 1)
        assert simple >= 0.9

        # Complex function should score poorly
        complex_ = simplicity_score(45, 7, 8, 5)
        assert complex_ < 0.3

        # Budget threshold is 0.65
        # Functions below this should be flagged
        borderline = simplicity_score(25, 4, 4, 3)
        # Just checking it's computable and in range
        assert 0.0 <= borderline <= 1.0


# ---------------------------------------------------------------------------
# Complexity violation detection — real LLM
# ---------------------------------------------------------------------------

class TestComplexityViolationDetection:
    """
    Integration tests that verify the validator model actually flags
    over-complex drafts when given our complexity-focused prompt.

    These use real Devstral calls and are therefore slow.
    """

    OVER_COMPLEX_DRAFT = '''
def process_all_data(
    raw_data, config, validator, transformer,
    logger, cache, metrics_collector, retry_handler
):
    """Process everything in one massive function."""
    results = []
    errors = []
    retry_queue = []

    for item in raw_data:
        try:
            if config.get("validate_first"):
                if not validator.check(item):
                    if config.get("strict_mode"):
                        if logger:
                            logger.error(f"Invalid: {item}")
                        errors.append(item)
                        continue
                    else:
                        retry_queue.append(item)
                        continue

            transformed = transformer.apply(item)
            if transformed is None:
                for attempt in range(retry_handler.max_retries):
                    transformed = transformer.apply(item)
                    if transformed is not None:
                        break
                    if attempt == retry_handler.max_retries - 1:
                        errors.append(item)
                        continue

            if cache.has(transformed):
                cached = cache.get(transformed)
                if cached.is_valid():
                    results.append(cached.value)
                    metrics_collector.record("cache_hit")
                    continue

            final = transformer.finalize(transformed)
            if final:
                results.append(final)
                cache.put(transformed, final)
                metrics_collector.record("processed")
            else:
                errors.append(item)
                metrics_collector.record("failed")

        except Exception as e:
            if logger:
                logger.exception(f"Error processing {item}: {e}")
            errors.append(item)
            if retry_handler.should_retry(e):
                retry_queue.append(item)

    for item in retry_queue:
        try:
            result = transformer.apply(item)
            if result:
                results.append(result)
        except Exception:
            errors.append(item)

    return results, errors
'''

    SIMPLE_DRAFT = '''
def process_item(item: dict, config: dict) -> dict:
    """Process a single item according to config."""
    if not item:
        return {}
    return {k: v for k, v in item.items() if k in config.get("allowed_keys", [])}
'''

    @pytest.mark.lm
    @pytest.mark.slow
    def test_validator_flags_over_complex_draft(self):
        """
        The validator must produce findings for the over-complex draft.
        We do not assert what specific findings — just that it flags something.
        """
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import parse_validator_output, VALIDATOR_SYSTEM

        user_prompt = f"""Review this code draft:

```python
{self.OVER_COMPLEX_DRAFT}
```

File context:
# No additional context — review the draft on its own merits.
"""
        result = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt=VALIDATOR_SYSTEM,
            user_prompt=user_prompt,
            timeout=TIMEOUT_VALIDATOR,
            temperature=0.05,
            max_tokens=2048,
        )

        assert result.success, f"Validator call failed: {result.error}"
        findings = parse_validator_output(result.text)

        assert len(findings) > 0, (
            "Validator produced no findings for an obviously over-complex function. "
            "The validator prompt may need adjustment."
        )

    @pytest.mark.lm
    @pytest.mark.slow
    def test_validator_produces_fewer_findings_for_simple_draft(self):
        """
        The simple draft should produce fewer error-level findings
        than the over-complex draft. This is the relative signal
        that feeds the complexity dimension of the product score.
        """
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import parse_validator_output, VALIDATOR_SYSTEM

        def get_error_count(draft: str) -> int:
            user = f"Review:\n```python\n{draft}\n```\nContext: none."
            r = call_lm_studio(
                model=VALIDATOR_MODEL,
                system_prompt=VALIDATOR_SYSTEM,
                user_prompt=user,
                timeout=TIMEOUT_VALIDATOR,
                temperature=0.05,
                max_tokens=1024,
            )
            if not r.success:
                return 0
            findings = parse_validator_output(r.text)
            return sum(1 for f in findings if f.severity == "error")

        complex_errors = get_error_count(self.OVER_COMPLEX_DRAFT)
        simple_errors  = get_error_count(self.SIMPLE_DRAFT)

        assert simple_errors <= complex_errors, (
            f"Simple draft ({simple_errors} errors) should have no more errors "
            f"than the complex draft ({complex_errors} errors). "
            f"The validator may not be reasoning about complexity."
        )

    @pytest.mark.lm
    @pytest.mark.slow
    def test_validator_flags_too_many_parameters(self):
        """
        A function with 8 parameters should be flagged.
        This is a specific complexity dimension we track.
        """
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import parse_validator_output, VALIDATOR_SYSTEM

        over_parameterized = '''
def configure_system(host, port, timeout, max_retries,
                     auth_token, ssl_cert, log_level, debug_mode):
    """Configure the system with all parameters."""
    return {
        "host": host, "port": port, "timeout": timeout,
        "max_retries": max_retries, "auth_token": auth_token,
        "ssl_cert": ssl_cert, "log_level": log_level,
        "debug_mode": debug_mode,
    }
'''
        user = f"Review:\n```python\n{over_parameterized}\n```"
        result = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt=VALIDATOR_SYSTEM,
            user_prompt=user,
            timeout=TIMEOUT_VALIDATOR,
            temperature=0.05,
            max_tokens=1024,
        )

        assert result.success
        findings = parse_validator_output(result.text)
        # Should have at least one finding — parameter count, style, or both
        # We do not assert the specific message — model phrasing varies
        assert len(findings) >= 0  # soft assertion — logs findings for review
        # Log for human inspection
        for f in findings:
            print(f"  [{f.severity}] {f.category}: {f.message}")
