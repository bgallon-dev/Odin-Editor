import pytest
import pathlib


pytestmark = [pytest.mark.lm, pytest.mark.slow]


class TestPipelineStructure:
    """
    Integration tests against real LM Studio models.
    These assert structural properties of the output,
    not specific content — model output is non-deterministic.
    """

    def test_pipeline_returns_result_object(self, incomplete_python_file):
        from pipeline import run_pipeline, PipelineResult
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        assert isinstance(result, PipelineResult)

    def test_successful_pipeline_has_nonempty_draft(self, incomplete_python_file):
        from pipeline import run_pipeline
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        if result.success:
            assert len(result.draft_text) > 0

    def test_draft_text_is_valid_python_when_successful(self, incomplete_python_file):
        """The drafter should produce syntactically valid Python."""
        import ast
        from pipeline import run_pipeline
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        if not result.success or not result.draft_text:
            pytest.skip("pipeline did not produce output")

        # Try to parse the draft as Python
        try:
            ast.parse(result.draft_text)
            valid = True
        except SyntaxError:
            # Try as a statement fragment inside a function
            try:
                ast.parse("def _wrapper():\n" +
                          "\n".join(f"    {line}"
                                    for line in result.draft_text.split("\n")))
                valid = True
            except SyntaxError:
                valid = False

        assert valid, f"Draft is not valid Python:\n{result.draft_text}"

    def test_confidence_score_in_valid_range(self, incomplete_python_file):
        from pipeline import run_pipeline
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        assert 0.0 <= result.confidence <= 1.0

    def test_findings_have_valid_structure(self, incomplete_python_file):
        from pipeline import run_pipeline, Finding
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        for finding in result.findings:
            assert isinstance(finding, Finding)
            assert finding.category in {
                "correctness", "style", "security", "performance"
            }
            assert finding.severity in {"error", "warning", "info"}
            assert isinstance(finding.line, int)
            assert finding.line >= 0
            assert isinstance(finding.message, str)
            assert len(finding.message) > 0

    def test_timing_fields_are_populated(self, incomplete_python_file):
        from pipeline import run_pipeline
        path, content = incomplete_python_file
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        assert result.drafter_ms >= 0
        assert result.validator_ms >= 0

    def test_pipeline_handles_empty_content(self):
        from pipeline import run_pipeline
        result = run_pipeline(
            file_path="empty.py",
            file_content="",
            cursor_offset=0,
        )
        # Should not raise — returns a result (success or not)
        assert isinstance(result.confidence, float)

    def test_pipeline_completes_within_timeout(self, incomplete_python_file):
        """Full pipeline should complete in under 120 seconds."""
        import time
        from pipeline import run_pipeline
        path, content = incomplete_python_file
        t0 = time.monotonic()
        result = run_pipeline(
            file_path=str(path),
            file_content=content,
            cursor_offset=len(content),
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 120, f"Pipeline took {elapsed:.1f}s — exceeded 120s budget"


class TestLMStudioConnection:

    def test_drafter_model_is_loaded(self):
        """Verify Granite is loaded in LM Studio."""
        from lm_studio import call_lm_studio, DRAFTER_MODEL, TIMEOUT_DRAFTER
        result = call_lm_studio(
            model=DRAFTER_MODEL,
            system_prompt="You are a helpful assistant.",
            user_prompt="Reply with the single word: READY",
            timeout=TIMEOUT_DRAFTER,
            max_tokens=10,
        )
        assert result.success, f"Drafter model failed: {result.error}"
        assert len(result.text) > 0

    def test_validator_model_is_loaded(self):
        """Verify Devstral is loaded in LM Studio."""
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        result = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt="You are a helpful assistant.",
            user_prompt="Reply with the single word: READY",
            timeout=TIMEOUT_VALIDATOR,
            max_tokens=10,
        )
        assert result.success, f"Validator model failed: {result.error}"
        assert len(result.text) > 0

    def test_drafter_respects_temperature(self):
        """At temperature 0.0 repeated calls should be near-identical."""
        from lm_studio import call_lm_studio, DRAFTER_MODEL, TIMEOUT_DRAFTER
        prompt = "def add(a: int, b: int) -> int:\n    "
        results = []
        for _ in range(3):
            r = call_lm_studio(
                model=DRAFTER_MODEL,
                system_prompt="Complete the Python function.",
                user_prompt=prompt,
                timeout=TIMEOUT_DRAFTER,
                temperature=0.0,
                max_tokens=20,
            )
            if r.success:
                results.append(r.text.strip())

        if len(results) >= 2:
            lengths = [len(r) for r in results]
            assert max(lengths) - min(lengths) < 50, \
                "Temperature 0.0 producing highly variable output"
