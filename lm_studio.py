"""
LM Studio HTTP client — stdlib-only, OpenAI-compatible endpoint.

All communication with LM Studio goes through this module.
Uses urllib (no openai package required).
"""

import json
import socket
import urllib.request
import urllib.error
from dataclasses import dataclass


LM_STUDIO_BASE = "http://127.0.0.1:1234"
DRAFTER_MODEL = "ibm/granite-4-h-tiny"  # fast MoE model
VALIDATOR_MODEL = "qwen/qwen3.5-9b"  # deep reasoning model
TIMEOUT_DRAFTER = 300  # seconds — includes prompt processing time
TIMEOUT_VALIDATOR = 1200  # seconds — Devstral is thorough


@dataclass
class LMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    success: bool
    error: str = ""
    reasoning_content: str = ""


def _debug(msg: str):
    print(f"[DEBUG][lm_studio] {msg}", flush=True)


def call_lm_studio(
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int,
    temperature: float = 0.15,
    max_tokens: int = 32000,
    frequency_penalty: float = 0.0,
    presence_penalty: float = 0.0,
) -> LMResponse:
    """Single call to the LM Studio OpenAI-compatible endpoint."""
    _debug(f"call_lm_studio START model={model} timeout={timeout}s "
           f"temp={temperature} max_tokens={max_tokens} "
           f"freq_pen={frequency_penalty} pres_pen={presence_penalty}")
    _debug(f"  system_prompt length={len(system_prompt)} chars")
    _debug(f"  user_prompt length={len(user_prompt)} chars")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "frequency_penalty": frequency_penalty,
        "presence_penalty": presence_penalty,
        "stream": False,
    }

    body = json.dumps(payload).encode("utf-8")
    _debug(f"  request body size={len(body)} bytes")
    req = urllib.request.Request(
        f"{LM_STUDIO_BASE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        _debug(f"  sending HTTP POST to {LM_STUDIO_BASE}/v1/chat/completions ...")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            _debug(f"  response received: {len(raw)} bytes, status={resp.status}")
            data = json.loads(raw)

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            text = message.get("content", "")
            reasoning = message.get("reasoning_content", "")

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

        _debug(f"  SUCCESS: text={len(text)} chars, reasoning={len(reasoning)} chars, "
               f"prompt_tokens={prompt_tokens}, completion_tokens={completion_tokens}")
        _debug(f"  response preview: {text[:200]!r}{'...' if len(text) > 200 else ''}")
        return LMResponse(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            reasoning_content=reasoning,
        )

    except (socket.timeout, TimeoutError):
        _debug(f"  TIMEOUT after {timeout}s for model={model}")
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"LM Studio timed out after {timeout}s",
        )

    except urllib.error.URLError as e:
        _debug(f"  CONNECTION FAILED: {e.reason}")
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"LM Studio connection failed: {e.reason}",
        )
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        _debug(f"  PARSE ERROR: {e}")
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"Unexpected response format: {e}",
        )
    except Exception as e:
        _debug(f"  UNEXPECTED ERROR: {e}")
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=str(e),
        )


def check_lm_studio_available() -> bool:
    """Quick health check — returns True if LM Studio is reachable."""
    _debug("check_lm_studio_available: pinging /v1/models ...")
    try:
        req = urllib.request.Request(
            f"{LM_STUDIO_BASE}/v1/models",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3):
            _debug("check_lm_studio_available: OK — reachable")
            return True
    except Exception as e:
        _debug(f"check_lm_studio_available: UNREACHABLE — {e}")
        return False
