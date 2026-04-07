import pytest
from pipeline import parse_validator_output, Finding, compute_confidence


class TestParseValidatorOutput:

    def test_parses_single_finding(self):
        output = '{"category": "correctness", "severity": "error", "line": 5, "message": "undefined name foo"}'
        findings = parse_validator_output(output)
        assert len(findings) == 1
        assert findings[0].category == "correctness"
        assert findings[0].severity == "error"
        assert findings[0].line == 5
        assert "foo" in findings[0].message

    def test_parses_multiple_findings(self):
        output = '\n'.join([
            '{"category": "correctness", "severity": "error", "line": 3, "message": "missing return"}',
            '{"category": "style", "severity": "warning", "line": 7, "message": "line too long"}',
            '{"category": "security", "severity": "info", "line": 12, "message": "consider validation"}',
        ])
        findings = parse_validator_output(output)
        assert len(findings) == 3
        assert findings[0].category == "correctness"
        assert findings[1].category == "style"
        assert findings[2].category == "security"

    def test_skips_ok_sentinel(self):
        output = '{"category": "ok", "severity": "info", "line": 0, "message": "No issues found"}'
        findings = parse_validator_output(output)
        assert len(findings) == 0

    def test_tolerates_malformed_lines(self):
        output = '\n'.join([
            '{"category": "correctness", "severity": "error", "line": 1, "message": "real issue"}',
            'this is not json at all',
            '{"category": "style", "severity": "warning", "line": 5, "message": "another real issue"}',
        ])
        findings = parse_validator_output(output)
        assert len(findings) == 2

    def test_tolerates_empty_output(self):
        findings = parse_validator_output("")
        assert findings == []

    def test_tolerates_whitespace_only(self):
        findings = parse_validator_output("   \n\n\t\n")
        assert findings == []

    def test_missing_fields_use_defaults(self):
        output = '{"message": "something went wrong"}'
        findings = parse_validator_output(output)
        assert len(findings) == 1
        assert findings[0].category == "correctness"
        assert findings[0].severity == "info"
        assert findings[0].line == 0


class TestConfidenceScoring:

    def test_no_findings_gives_high_confidence(self):
        score = compute_confidence("def foo():\n    return 42\n", [])
        assert score == 1.0

    def test_error_finding_reduces_confidence(self):
        findings = [Finding("correctness", "error", 1, "undefined name")]
        score = compute_confidence("def foo(): pass", findings)
        assert score < 1.0
        assert score == pytest.approx(0.75)

    def test_multiple_errors_stack(self):
        findings = [
            Finding("correctness", "error", 1, "issue 1"),
            Finding("correctness", "error", 2, "issue 2"),
            Finding("correctness", "error", 3, "issue 3"),
            Finding("correctness", "error", 4, "issue 4"),
        ]
        score = compute_confidence("def foo(): pass", findings)
        assert score == 0.0

    def test_warning_deducts_less_than_error(self):
        error_findings = [Finding("correctness", "error", 1, "e")]
        warning_findings = [Finding("style", "warning", 1, "w")]
        error_score = compute_confidence("code that is long enough", error_findings)
        warning_score = compute_confidence("code that is long enough", warning_findings)
        assert warning_score > error_score

    def test_very_short_draft_penalized(self):
        score = compute_confidence("x", [])
        assert score < 1.0

    def test_score_never_exceeds_1(self):
        score = compute_confidence("long draft " * 100, [])
        assert score <= 1.0

    def test_score_never_below_0(self):
        findings = [Finding("correctness", "error", i, "e") for i in range(20)]
        score = compute_confidence("x", findings)
        assert score >= 0.0
