"""
Drafter-Validator pipeline.

Stage 1: Granite 4.0 Tiny H produces a code completion at the cursor.
Stage 2: Devstral Small 2 reviews the draft for correctness/style/security/performance.

Returns a PipelineResult containing the draft, confidence, and findings.
"""

import json
import time
from dataclasses import dataclass, field


def _debug(msg: str):
    print(f"[DEBUG][pipeline] {msg}", flush=True)


from lm_studio import (
    call_lm_studio,
    LMResponse,
    DRAFTER_MODEL,
    VALIDATOR_MODEL,
    TIMEOUT_DRAFTER,
    TIMEOUT_VALIDATOR,
)
from prompts import DRAFTER_SYSTEM, DRAFTER_USER, VALIDATOR_SYSTEM, VALIDATOR_USER


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
    structural_score: float = 1.0  # composite from structural gate (1.0 = no gate or passed)


# ---------------------------------------------------------------------------
# Draft sanitization
# ---------------------------------------------------------------------------


def sanitize_draft(text: str) -> str:
    """Unescape and clean LLM draft output."""
    _debug(f"sanitize_draft START: input={len(text)} chars, lines={text.count(chr(10))}")
    _debug(f"  raw input preview: {text[:200]!r}{'...' if len(text) > 200 else ''}")

    # Phase 1: Fix double-escaped JSON artifacts
    # The model sometimes outputs \\n instead of real newlines
    if '\\n' in text and '\n' not in text:
        _debug("  Phase 1: detected escaped newlines — unescaping")
        text = text.replace('\\n', '\n')
        text = text.replace('\\t', '\t')
        text = text.replace('\\"', '"')
        text = text.replace("\\'", "'")
    else:
        _debug("  Phase 1: no escaped newlines detected")

    # Phase 2: Strip markdown fences
    lines = text.split('\n')
    had_fences = False
    if lines and lines[0].strip().startswith('```'):
        _debug(f"  Phase 2: stripping opening fence: {lines[0]!r}")
        lines = lines[1:]
        had_fences = True
    if lines and lines[-1].strip().startswith('```'):
        _debug(f"  Phase 2: stripping closing fence: {lines[-1]!r}")
        lines = lines[:-1]
        had_fences = True
    if not had_fences:
        _debug("  Phase 2: no markdown fences found")

    # Phase 3: Trim leading/trailing blank lines
    while lines and not lines[0].strip():
        lines = lines[0:]  # keep one leading blank at most
        break
    while lines and not lines[-1].strip():
        lines.pop()

    result = '\n'.join(lines)
    _debug(f"sanitize_draft END: output={len(result)} chars, lines={result.count(chr(10)) + 1}")
    _debug(f"  sanitized preview: {result[:200]!r}{'...' if len(result) > 200 else ''}")
    return result


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------


def assemble_drafter_context(
    file_path: str,
    file_content: str,
    cursor_offset: int,
) -> str:
    """Insert <CURSOR> marker at cursor offset, truncate to local context."""
    MAX_BEFORE = 4_000   # ~100 lines of context before cursor
    MAX_AFTER  = 2_000   # ~50 lines of context after cursor

    _debug(f"assemble_drafter_context: file={file_path} content={len(file_content)} chars, cursor_offset={cursor_offset}")

    offset = max(0, min(cursor_offset, len(file_content)))
    before = file_content[:offset]
    after = file_content[offset:]
    _debug(f"  before_cursor={len(before)} chars, after_cursor={len(after)} chars")

    # Trim to local window
    if len(before) > MAX_BEFORE:
        _debug(f"  trimming before from {len(before)} to ~{MAX_BEFORE} chars")
        cut = before[-(MAX_BEFORE):]
        nl = cut.find('\n')
        if nl >= 0:
            cut = cut[nl + 1:]
        before = cut

    if len(after) > MAX_AFTER:
        _debug(f"  trimming after from {len(after)} to ~{MAX_AFTER} chars")
        cut = after[:MAX_AFTER]
        nl = cut.rfind('\n')
        if nl >= 0:
            cut = cut[:nl]
        after = cut

    result = before + "<CURSOR>" + after
    _debug(f"  assembled context: {len(result)} chars total")
    return result


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
# Symbol-context parsing
# ---------------------------------------------------------------------------


def _parse_symbol_names(symbol_context: str) -> set[str]:
    """Extract symbol names from the '  {kind} {signature}' format."""
    names: set[str] = set()
    for line in symbol_context.strip().splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            # Format: "kind signature" — extract name before '('
            name = parts[1].split('(')[0].strip()
            if name:
                names.add(name)
    return names


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
    _debug("=" * 60)
    _debug(f"run_pipeline START: file={file_path} content={len(file_content)} chars "
           f"cursor_offset={cursor_offset}")
    _debug(f"  symbol_context={len(symbol_context)} chars, past_findings={len(past_findings)} chars")
    pipeline_t0 = time.monotonic()

    # --- Stage 1: Drafter ---
    _debug("--- Stage 1: DRAFTER ---")
    context_with_cursor = assemble_drafter_context(
        file_path, file_content, cursor_offset
    )

    drafter_user = DRAFTER_USER.format(
        file_path=file_path,
        content_with_cursor=context_with_cursor,
    )
    _debug(f"  drafter prompt assembled: {len(drafter_user)} chars")

    t0 = time.monotonic()
    drafter_resp: LMResponse = call_lm_studio(
        model=DRAFTER_MODEL,
        system_prompt=DRAFTER_SYSTEM,
        user_prompt=drafter_user,
        timeout=TIMEOUT_DRAFTER,
        temperature=0.15,
        max_tokens=512,
        frequency_penalty=0.8,
        presence_penalty=0.6,
    )
    drafter_ms = int((time.monotonic() - t0) * 1000)
    _debug(f"  drafter returned: success={drafter_resp.success} "
           f"time={drafter_ms}ms text={len(drafter_resp.text)} chars "
           f"error={drafter_resp.error!r}")

    if not drafter_resp.success:
        _debug(f"  EARLY EXIT: drafter failed — {drafter_resp.error}")
        return PipelineResult(
            success=False,
            draft_text="",
            confidence=0.0,
            error=f"Drafter failed: {drafter_resp.error}",
            drafter_ms=drafter_ms,
        )

    draft_text = sanitize_draft(drafter_resp.text.strip())
    if not draft_text:
        _debug("  EARLY EXIT: drafter returned empty after sanitization")
        return PipelineResult(
            success=False,
            draft_text="",
            confidence=0.0,
            error="Drafter returned empty response",
            drafter_ms=drafter_ms,
        )

    _debug(f"  draft after sanitize: {len(draft_text)} chars, "
           f"{draft_text.count(chr(10)) + 1} lines")

    # --- Stage 1.5: Structural Gate (Odin) ---
    gate_result = None
    if file_path.endswith(".odin"):
        _debug("--- Stage 1.5: STRUCTURAL GATE (Odin) ---")
        from odin_structural_gate import OdinStructuralGate
        gate = OdinStructuralGate()
        known_syms = _parse_symbol_names(symbol_context)
        _debug(f"  known_symbols from context: {len(known_syms)} — {sorted(known_syms)[:10]}")
        gate_result = gate.score(draft_text, file_content, cursor_offset, known_syms)
        _debug(f"  gate result: braces={gate_result.braces_balanced} "
               f"escaped={gate_result.no_escaped_newlines} "
               f"sym_overlap={gate_result.symbol_overlap:.3f} "
               f"imports={gate_result.import_preserved:.3f} "
               f"composite={gate_result.composite:.4f} "
               f"hard_reject={gate_result.hard_reject}")

        if gate_result.hard_reject:
            _debug("  EARLY EXIT: structural gate HARD REJECT (unbalanced braces)")
            return PipelineResult(
                success=False,
                draft_text=draft_text,
                confidence=0.0,
                findings=[Finding("correctness", "error", 0,
                          "Structural gate: unbalanced braces")],
                drafter_ms=drafter_ms,
                structural_score=gate_result.composite,
                error="Draft failed structural validation",
            )
    else:
        _debug("  Stage 1.5: skipped (not an .odin file)")

    # --- Stage 2: Validator ---
    _debug("--- Stage 2: VALIDATOR ---")
    context_snippet = extract_context_snippet(file_content, cursor_offset)
    _debug(f"  context_snippet: {len(context_snippet)} chars")

    symbol_section = ""
    if symbol_context:
        symbol_section = f"\nKnown symbols in this file:\n{symbol_context}"
        _debug(f"  symbol_section: {len(symbol_section)} chars")

    past_section = ""
    if past_findings:
        past_section = f"\nPreviously flagged issues in this file:\n{past_findings}"
        _debug(f"  past_section: {len(past_section)} chars")

    validator_user = VALIDATOR_USER.format(
        draft_text=draft_text,
        context_snippet=context_snippet,
        symbol_section=symbol_section,
        past_section=past_section,
    )
    _debug(f"  validator prompt assembled: {len(validator_user)} chars")

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
    _debug(f"  validator returned: success={validator_resp.success} "
           f"time={validator_ms}ms text={len(validator_resp.text)} chars "
           f"reasoning={len(validator_resp.reasoning_content)} chars "
           f"error={validator_resp.error!r}")

    # Primary: parse from content. Fallback: if content is empty but
    # reasoning_content has JSON-shaped lines (thinking model used all
    # tokens on reasoning), try to extract findings from there.
    validator_text = ""
    if validator_resp.success:
        validator_text = validator_resp.text.strip()
        if not validator_text and validator_resp.reasoning_content:
            _debug("  validator content empty, falling back to reasoning_content")
            validator_text = validator_resp.reasoning_content
    _debug(f"  validator_text for parsing: {len(validator_text)} chars")
    _debug(f"  validator output preview: {validator_text[:300]!r}{'...' if len(validator_text) > 300 else ''}")

    findings = parse_validator_output(validator_text)
    validator_failed = validator_resp.success and not validator_resp.text.strip()
    _debug(f"  parsed findings: {len(findings)} items, validator_failed={validator_failed}")
    for i, f in enumerate(findings):
        _debug(f"    finding[{i}]: {f.severity} {f.category} L{f.line} — {f.message}")

    confidence = compute_confidence(draft_text, findings, validator_failed)
    _debug(f"  base confidence: {confidence:.3f}")

    # Apply structural gate penalty (soft failures)
    if gate_result is not None:
        pre_gate_conf = confidence
        confidence *= gate_result.composite
        confidence = max(0.0, min(1.0, confidence))
        _debug(f"  structural gate penalty: {pre_gate_conf:.3f} * {gate_result.composite:.4f} = {confidence:.3f}")

    structural_score_val = gate_result.composite if gate_result else 1.0

    total_ms = int((time.monotonic() - pipeline_t0) * 1000)
    _debug(f"run_pipeline END: success=True confidence={confidence:.3f} "
           f"structural={structural_score_val:.4f} "
           f"drafter={drafter_ms}ms validator={validator_ms}ms total={total_ms}ms")
    _debug("=" * 60)

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
        structural_score=structural_score_val,
    )


# ---------------------------------------------------------------------------
# Validator output parsing
# ---------------------------------------------------------------------------


def parse_validator_output(text: str) -> list[Finding]:
    """Parse line-delimited JSON findings. Tolerates malformed output."""
    _debug(f"parse_validator_output: input={len(text)} chars")
    findings = []
    lines = text.strip().split("\n")
    _debug(f"  total lines to parse: {len(lines)}")
    for idx, line in enumerate(lines):
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
                _debug(f"  line {idx}: OK finding — skipped")
                continue
            findings.append(finding)
        except (json.JSONDecodeError, ValueError) as e:
            _debug(f"  line {idx}: PARSE FAILED ({e}): {line[:100]!r}")
            continue
    _debug(f"parse_validator_output: {len(findings)} findings extracted")
    return findings


def compute_confidence(
    draft_text: str, findings: list[Finding], validator_failed: bool = False
) -> float:
    """Heuristic confidence score 0.0–1.0."""
    _debug(f"compute_confidence: draft_len={len(draft_text)} "
           f"findings={len(findings)} validator_failed={validator_failed}")

    # If the validator produced no usable output, we can't trust the draft
    if validator_failed:
        _debug("  validator_failed=True ->returning 0.2")
        return 0.2

    score = 1.0
    for f in findings:
        if f.severity == "error":
            score -= 0.25
            _debug(f"  error finding: -{0.25} ->{score:.2f}")
        elif f.severity == "warning":
            score -= 0.10
            _debug(f"  warning finding: -{0.10} ->{score:.2f}")
    if len(draft_text) < 10:
        score -= 0.30
        _debug(f"  short draft penalty: -{0.30} ->{score:.2f}")
    result = max(0.0, min(1.0, score))
    _debug(f"  final confidence: {result:.3f}")
    return result
