import pytest
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.lm, pytest.mark.slow, pytest.mark.eval]


class TestValidatorQuality:
    """
    Eval tests for the validator stage.
    Checks that the validator produces structured, parseable findings
    and that its output has expected properties.
    """

    def test_validator_produces_valid_json_findings(self):
        """Each line of validator output should be valid JSON with the right schema."""
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import VALIDATOR_SYSTEM, VALIDATOR_USER, parse_validator_output

        draft = "def foo(x):\n    return x + 1\n"
        context = "# A simple module\n"

        user_prompt = VALIDATOR_USER.format(
            draft_text=draft,
            context_snippet=context,
        )

        resp = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt=VALIDATOR_SYSTEM,
            user_prompt=user_prompt,
            timeout=TIMEOUT_VALIDATOR,
            temperature=0.05,
            max_tokens=2048,
        )

        assert resp.success, f"Validator call failed: {resp.error}"
        assert len(resp.text.strip()) > 0, "Validator returned empty output"

        # The output should be parseable by our parser
        findings = parse_validator_output(resp.text)
        # Even if there are no issues, parse should not crash
        assert isinstance(findings, list)

    def test_validator_catches_obvious_bug(self):
        """Given code with an obvious NameError, the validator should flag it."""
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import VALIDATOR_SYSTEM, VALIDATOR_USER, parse_validator_output

        draft = "def compute(x):\n    return undeclared_variable + x\n"
        context = "# No imports\n"

        user_prompt = VALIDATOR_USER.format(
            draft_text=draft,
            context_snippet=context,
        )

        resp = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt=VALIDATOR_SYSTEM,
            user_prompt=user_prompt,
            timeout=TIMEOUT_VALIDATOR,
            temperature=0.05,
            max_tokens=2048,
        )

        if not resp.success:
            pytest.skip(f"Validator call failed: {resp.error}")

        findings = parse_validator_output(resp.text)
        # The validator should have found at least one issue
        assert len(findings) > 0, (
            "Validator did not flag undeclared_variable. "
            f"Raw output: {resp.text[:500]}"
        )
        # At least one finding should mention correctness or the variable name
        categories = [f.category for f in findings]
        messages = " ".join(f.message.lower() for f in findings)
        has_correctness = "correctness" in categories
        has_name_ref = "undeclared" in messages or "undefined" in messages or "name" in messages
        assert has_correctness or has_name_ref, (
            f"Validator flagged issues but none about the undefined name. "
            f"Categories: {categories}, Messages: {messages}"
        )

    def test_validator_gives_ok_for_clean_code(self):
        """Given clean, correct code, the validator should find few or no issues."""
        from lm_studio import call_lm_studio, VALIDATOR_MODEL, TIMEOUT_VALIDATOR
        from pipeline import VALIDATOR_SYSTEM, VALIDATOR_USER, parse_validator_output

        draft = (
            "def add(a: int, b: int) -> int:\n"
            '    """Return the sum of a and b."""\n'
            "    return a + b\n"
        )
        context = "from typing import Optional\n"

        user_prompt = VALIDATOR_USER.format(
            draft_text=draft,
            context_snippet=context,
        )

        resp = call_lm_studio(
            model=VALIDATOR_MODEL,
            system_prompt=VALIDATOR_SYSTEM,
            user_prompt=user_prompt,
            timeout=TIMEOUT_VALIDATOR,
            temperature=0.05,
            max_tokens=2048,
        )

        if not resp.success:
            pytest.skip(f"Validator call failed: {resp.error}")

        findings = parse_validator_output(resp.text)
        error_count = sum(1 for f in findings if f.severity == "error")
        # Clean code should have zero errors (warnings/info acceptable)
        assert error_count == 0, (
            f"Validator found {error_count} errors in clean code. "
            f"Findings: {[(f.severity, f.message) for f in findings]}"
        )
