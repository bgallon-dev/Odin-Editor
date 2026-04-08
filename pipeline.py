"""
Drafter-Validator pipeline.

Stage 1: Granite 4.0 Tiny H produces a code completion at the cursor.
Stage 2: Devstral Small 2 reviews the draft for correctness/style/security/performance.

Returns a PipelineResult containing the draft, confidence, and findings.
"""

import json
import time
from dataclasses import dataclass, field

from lm_studio import (
    call_lm_studio,
    LMResponse,
    DRAFTER_MODEL,
    VALIDATOR_MODEL,
    TIMEOUT_DRAFTER,
    TIMEOUT_VALIDATOR,
)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

DRAFTER_SYSTEM = """You are an expert code completion assistant embedded in a code editor.
You receive a file's complete content with a <CURSOR> marker showing where the user is editing.
Your task is to suggest what should be written at the cursor position.

Rules:
- Produce only the code to insert at <CURSOR>. No explanation, no markdown fences.
- Match the surrounding code's style, indentation, and conventions exactly.
- If completing a function, complete it fully.
- If the cursor is mid-expression, complete the expression.
- Output nothing if no meaningful completion is possible.
"""

DRAFTER_USER = """File: {file_path}

{content_with_cursor}

Complete the code at <CURSOR>:"""


VALIDATOR_SYSTEM = """You are a structural code reviewer. You receive a proposed code draft
and must identify concrete issues across four categories.

CRITICAL: Do not use a thinking trace or internal reasoning. Output ONLY the JSON lines immediately.

For each issue found, output a JSON object on its own line with this exact schema:
{{"category": "correctness|style|security|performance", "severity": "error|warning|info", "line": <int>, "message": "<string>"}}

Output ONLY these JSON lines — no prose, no explanation, no markdown.
If no issues are found, output a single line: {{"category": "ok", "severity": "info", "line": 0, "message": "No issues found"}}

Categories:
- correctness: logic errors, undefined names, wrong types, missing returns
- style: naming conventions, line length, docstring completeness
- security: injection risks, unsafe operations, credential exposure
- performance: unnecessary allocations, redundant operations, complexity issues
"""

VALIDATOR_USER = """Review this code draft:

```
{draft_text}
```

File context (lines around cursor):
{context_snippet}
{symbol_section}{past_section}"""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    category: str
    severity: str
    line: int
    message: str


@dataclass
class PipelineResult:
    success: bool
    draft_text: str
    confidence: float
    findings: list[Finding] = field(default_factory=list)
    drafter_tokens: int = 0
    validator_tokens: int = 0
    drafter_ms: int = 0
    validator_ms: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def assemble_drafter_context(
    file_path: str,
    file_content: str,
    cursor_offset: int,
) -> str:
    """Insert <CURSOR> marker at cursor offset, truncate if needed."""
    MAX_CHARS = 24_000  # conservative for Granite context window

    offset = max(0, min(cursor_offset, len(file_content)))
    before = file_content[:offset]
    after = file_content[offset:]

    combined = before + "<CURSOR>" + after
    if len(combined) <= MAX_CHARS:
        return combined

    # Trim symmetrically around the cursor
    half = (MAX_CHARS - len("<CURSOR>")) // 2
    before = before[-half:] if len(before) > half else before
    after = after[:half] if len(after) > half else after
    return before + "<CURSOR>" + after


def extract_context_snippet(
    file_content: str, cursor_offset: int = 0, max_lines: int = 50
) -> str:
    """Lines centered around cursor position for the validator."""
    lines = file_content.split("\n")
    if not lines:
        return ""
    cursor_line = file_content[:cursor_offset].count("\n")
    half = max_lines // 2
    start = max(0, cursor_line - half)
    end = min(len(lines), start + max_lines)
    start = max(0, end - max_lines)
    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def run_pipeline(
    file_path: str,
    file_content: str,
    cursor_offset: int,
    symbol_context: str = "",
    past_findings: str = "",
) -> PipelineResult:
    """Run drafter then validator. Returns a PipelineResult."""

    # --- Stage 1: Drafter ---
    context_with_cursor = assemble_drafter_context(
        file_path, file_content, cursor_offset
    )

    drafter_user = DRAFTER_USER.format(
        file_path=file_path,
        content_with_cursor=context_with_cursor,
    )

    t0 = time.monotonic()
    drafter_resp: LMResponse = call_lm_studio(
        model=DRAFTER_MODEL,
        system_prompt=DRAFTER_SYSTEM,
        user_prompt=drafter_user,
        timeout=TIMEOUT_DRAFTER,
        temperature=0.15,
        max_tokens=1024,
        frequency_penalty=0.8,
        presence_penalty=0.6,
    )
    drafter_ms = int((time.monotonic() - t0) * 1000)

    if not drafter_resp.success:
        return PipelineResult(
            success=False,
            draft_text="",
            confidence=0.0,
            error=f"Drafter failed: {drafter_resp.error}",
            drafter_ms=drafter_ms,
        )

    draft_text = drafter_resp.text.strip()
    if not draft_text:
        return PipelineResult(
            success=False,
            draft_text="",
            confidence=0.0,
            error="Drafter returned empty response",
            drafter_ms=drafter_ms,
        )

    # --- Stage 2: Validator ---
    context_snippet = extract_context_snippet(file_content, cursor_offset)

    symbol_section = ""
    if symbol_context:
        symbol_section = f"\nKnown symbols in this file:\n{symbol_context}"

    past_section = ""
    if past_findings:
        past_section = f"\nPreviously flagged issues in this file:\n{past_findings}"

    validator_user = VALIDATOR_USER.format(
        draft_text=draft_text,
        context_snippet=context_snippet,
        symbol_section=symbol_section,
        past_section=past_section,
    )

    t1 = time.monotonic()
    validator_resp: LMResponse = call_lm_studio(
        model=VALIDATOR_MODEL,
        system_prompt=VALIDATOR_SYSTEM,
        user_prompt=validator_user,
        timeout=TIMEOUT_VALIDATOR,
        temperature=0.05,
        max_tokens=32000,
    )
    validator_ms = int((time.monotonic() - t1) * 1000)

    # Primary: parse from content. Fallback: if content is empty but
    # reasoning_content has JSON-shaped lines (thinking model used all
    # tokens on reasoning), try to extract findings from there.
    validator_text = ""
    if validator_resp.success:
        validator_text = validator_resp.text.strip()
        if not validator_text and validator_resp.reasoning_content:
            validator_text = validator_resp.reasoning_content

    findings = parse_validator_output(validator_text)
    validator_failed = validator_resp.success and not validator_resp.text.strip()

    confidence = compute_confidence(draft_text, findings, validator_failed)

    return PipelineResult(
        success=True,
        draft_text=draft_text,
        confidence=confidence,
        findings=findings,
        drafter_tokens=drafter_resp.prompt_tokens + drafter_resp.completion_tokens,
        validator_tokens=validator_resp.prompt_tokens
        + validator_resp.completion_tokens,
        drafter_ms=drafter_ms,
        validator_ms=validator_ms,
    )


# ---------------------------------------------------------------------------
# Validator output parsing
# ---------------------------------------------------------------------------


def parse_validator_output(text: str) -> list[Finding]:
    """Parse line-delimited JSON findings. Tolerates malformed output."""
    findings = []
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            finding = Finding(
                category=data.get("category", "correctness"),
                severity=data.get("severity", "info"),
                line=int(data.get("line", 0)),
                message=data.get("message", ""),
            )
            if finding.category == "ok":
                continue
            findings.append(finding)
        except (json.JSONDecodeError, ValueError):
            continue
    return findings


def compute_confidence(
    draft_text: str, findings: list[Finding], validator_failed: bool = False
) -> float:
    """Heuristic confidence score 0.0–1.0."""
    # If the validator produced no usable output, we can't trust the draft
    if validator_failed:
        return 0.2

    score = 1.0
    for f in findings:
        if f.severity == "error":
            score -= 0.25
        elif f.severity == "warning":
            score -= 0.10
    if len(draft_text) < 10:
        score -= 0.30
    return max(0.0, min(1.0, score))
