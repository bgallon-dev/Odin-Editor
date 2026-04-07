import pytest
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

pytestmark = [pytest.mark.lm, pytest.mark.slow, pytest.mark.eval]

DB_PATH = ".kettle/memory.db"


class TestDrafterQuality:
    """
    Evaluation tests — these record scores but do not hard-fail
    unless the output is structurally broken in a fundamental way.

    The composite score is what matters over time, not individual runs.
    Query the eval_runs table to see trends.
    """

    def test_eval_corpus_all_cases(self):
        from pipeline import run_pipeline
        from tests.evals.harness import (
            load_corpus, score_result, store_scores, print_score_report
        )

        cases = load_corpus()
        if not cases:
            pytest.skip("No eval corpus files found in tests/evals/corpus/")

        scores = []
        for case in cases:
            content = case.file_path.read_text(encoding="utf-8")
            result = run_pipeline(
                file_path=str(case.file_path),
                file_content=content,
                cursor_offset=case.cursor_offset,
            )
            score = score_result(case, result)
            scores.append(score)

        # Store results for longitudinal tracking
        try:
            store_scores(scores, DB_PATH)
        except Exception as e:
            print(f"Warning: could not store eval scores: {e}")

        print_score_report(scores)

        # Hard assertions — these represent absolute minimums
        # A composite below 0.3 means the pipeline is fundamentally broken
        mean_composite = sum(s.composite_score for s in scores) / len(scores)
        assert mean_composite >= 0.3, (
            f"Mean composite score {mean_composite:.3f} is below minimum threshold 0.30. "
            f"The pipeline is producing fundamentally broken output."
        )

        # At least half of cases should produce non-empty output
        nonempty_rate = sum(1 for s in scores if s.nonempty > 0) / len(scores)
        assert nonempty_rate >= 0.5, (
            f"Only {nonempty_rate:.0%} of cases produced non-empty drafts. "
            f"Check that models are loaded and LM Studio is running correctly."
        )

    def test_cursor_marker_never_leaks_into_draft(self):
        """
        The <CURSOR> marker is an implementation detail.
        It must never appear in the output shown to the user.
        This is a hard correctness requirement, not a quality metric.
        """
        from pipeline import run_pipeline
        from tests.evals.harness import load_corpus

        cases = load_corpus()
        if not cases:
            pytest.skip("No eval corpus files found")

        for case in cases:
            content = case.file_path.read_text(encoding="utf-8")
            result = run_pipeline(
                file_path=str(case.file_path),
                file_content=content,
                cursor_offset=case.cursor_offset,
            )
            if result.success and result.draft_text:
                assert "<CURSOR>" not in result.draft_text, (
                    f"Case {case.name}: <CURSOR> marker leaked into draft output. "
                    f"The system prompt must instruct the model not to reproduce it."
                )

    def test_drafter_latency_within_budget(self):
        """
        Drafter should complete in under 30 seconds.
        If it takes longer, Granite may not be the active model
        or hardware is under pressure.
        """
        from pipeline import run_pipeline
        from tests.evals.harness import load_corpus

        cases = load_corpus()
        if not cases:
            pytest.skip("No eval corpus files found")

        # Test against the first case only for latency
        case = cases[0]
        content = case.file_path.read_text(encoding="utf-8")
        result = run_pipeline(
            file_path=str(case.file_path),
            file_content=content,
            cursor_offset=case.cursor_offset,
        )
        assert result.drafter_ms < 30_000, (
            f"Drafter took {result.drafter_ms}ms — exceeded 30s budget. "
            f"Check that Granite 4.0 Tiny H is the active drafter model."
        )
