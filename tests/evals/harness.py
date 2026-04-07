"""
Evaluation harness for the drafter-validator pipeline.

Runs the pipeline against a fixed corpus of Python files and scores
the output on structural quality dimensions. Results are stored in
the project database for longitudinal analysis.

Run with: pytest tests/evals/ -v --tb=short
"""
import ast
import json
import sqlite3
import time
import pathlib
from dataclasses import dataclass, field
from typing import Optional

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"


@dataclass
class EvalCase:
    name:           str
    file_path:      pathlib.Path
    cursor_hint:    str   # description of where cursor is
    cursor_offset:  int   # exact offset in the file


@dataclass
class EvalScore:
    case_name:        str
    success:          bool
    ast_valid:        float   # 1.0 or 0.0
    nonempty:         float   # 1.0 or 0.0
    no_cursor_marker: float   # draft should not contain <CURSOR>
    findings_parseable: float # validator output was valid structured JSON
    finding_count:    int
    error_count:      int
    warning_count:    int
    confidence:       float
    drafter_ms:       int
    validator_ms:     int
    draft_length:     int
    timestamp:        float = field(default_factory=time.time)

    @property
    def composite_score(self) -> float:
        """
        Weighted composite of structural quality dimensions.
        This is the primary metric tracked over time.
        """
        return (
            self.ast_valid        * 0.40 +
            self.nonempty         * 0.20 +
            self.no_cursor_marker * 0.20 +
            self.findings_parseable * 0.20
        )


def load_corpus() -> list[EvalCase]:
    """Load all eval cases from the corpus directory."""
    cases = []
    for py_file in sorted(CORPUS_DIR.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        # Cursor goes at the end of the file — typical completion scenario
        cases.append(EvalCase(
            name=py_file.stem,
            file_path=py_file,
            cursor_hint="end of file",
            cursor_offset=len(content),
        ))
    return cases


def score_result(case: EvalCase, result) -> EvalScore:
    """Score a pipeline result against structural quality criteria."""
    if not result.success or not result.draft_text:
        return EvalScore(
            case_name=case.name,
            success=False,
            ast_valid=0.0,
            nonempty=0.0,
            no_cursor_marker=1.0,
            findings_parseable=0.0,
            finding_count=0,
            error_count=0,
            warning_count=0,
            confidence=0.0,
            drafter_ms=result.drafter_ms,
            validator_ms=result.validator_ms,
            draft_length=0,
        )

    # AST validity
    ast_valid = 0.0
    try:
        ast.parse(result.draft_text)
        ast_valid = 1.0
    except SyntaxError:
        try:
            wrapped = "def _w():\n" + "\n".join(
                f"    {l}" for l in result.draft_text.split("\n")
            )
            ast.parse(wrapped)
            ast_valid = 0.5  # valid as fragment, not standalone
        except SyntaxError:
            ast_valid = 0.0

    # No cursor marker leaked into draft
    no_cursor = 0.0 if "<CURSOR>" in result.draft_text else 1.0

    # Findings were parseable structured JSON
    findings_parseable = 1.0 if result.findings is not None else 0.0

    error_count = sum(1 for f in result.findings if f.severity == "error")
    warning_count = sum(1 for f in result.findings if f.severity == "warning")

    return EvalScore(
        case_name=case.name,
        success=True,
        ast_valid=ast_valid,
        nonempty=1.0 if len(result.draft_text.strip()) > 0 else 0.0,
        no_cursor_marker=no_cursor,
        findings_parseable=findings_parseable,
        finding_count=len(result.findings),
        error_count=error_count,
        warning_count=warning_count,
        confidence=result.confidence,
        drafter_ms=result.drafter_ms,
        validator_ms=result.validator_ms,
        draft_length=len(result.draft_text),
    )


def store_scores(scores: list[EvalScore], db_path: str):
    """
    Append eval scores to the eval history table.
    Creates the table if it does not exist.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eval_runs (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp        REAL    NOT NULL,
            case_name        TEXT    NOT NULL,
            success          INTEGER NOT NULL,
            ast_valid        REAL    NOT NULL,
            nonempty         REAL    NOT NULL,
            no_cursor_marker REAL    NOT NULL,
            findings_parseable REAL  NOT NULL,
            composite_score  REAL    NOT NULL,
            finding_count    INTEGER NOT NULL,
            error_count      INTEGER NOT NULL,
            warning_count    INTEGER NOT NULL,
            confidence       REAL    NOT NULL,
            drafter_ms       INTEGER NOT NULL,
            validator_ms     INTEGER NOT NULL,
            draft_length     INTEGER NOT NULL
        )
    """)
    for score in scores:
        conn.execute("""
            INSERT INTO eval_runs VALUES (
                NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            score.timestamp,
            score.case_name,
            int(score.success),
            score.ast_valid,
            score.nonempty,
            score.no_cursor_marker,
            score.findings_parseable,
            score.composite_score,
            score.finding_count,
            score.error_count,
            score.warning_count,
            score.confidence,
            score.drafter_ms,
            score.validator_ms,
            score.draft_length,
        ))
    conn.commit()
    conn.close()


def print_score_report(scores: list[EvalScore]):
    """Print a human-readable score report after the eval run."""
    print("\n" + "=" * 60)
    print("EVAL RUN REPORT")
    print("=" * 60)

    for score in scores:
        status = "PASS" if score.success else "FAIL"
        print(f"\n[{status}] {score.case_name}")
        print(f"  Composite:    {score.composite_score:.2f}")
        print(f"  AST valid:    {score.ast_valid:.2f}")
        print(f"  Non-empty:    {score.nonempty:.2f}")
        print(f"  No cursor:    {score.no_cursor_marker:.2f}")
        print(f"  Confidence:   {score.confidence:.2f}")
        print(f"  Findings:     {score.finding_count} "
              f"({score.error_count} errors, {score.warning_count} warnings)")
        print(f"  Drafter:      {score.drafter_ms}ms")
        print(f"  Validator:    {score.validator_ms}ms")
        print(f"  Draft length: {score.draft_length} chars")

    mean_composite = sum(s.composite_score for s in scores) / len(scores) if scores else 0
    mean_drafter = sum(s.drafter_ms for s in scores) / len(scores) if scores else 0
    mean_validator = sum(s.validator_ms for s in scores) / len(scores) if scores else 0

    print(f"\n{'=' * 60}")
    print(f"SUMMARY — {len(scores)} cases")
    print(f"  Mean composite score: {mean_composite:.3f}")
    print(f"  Mean drafter latency: {mean_drafter:.0f}ms")
    print(f"  Mean validator latency: {mean_validator:.0f}ms")
    print("=" * 60)
