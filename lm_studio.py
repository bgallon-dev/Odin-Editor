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
    req = urllib.request.Request(
        f"{LM_STUDIO_BASE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            text = message.get("content", "")
            reasoning = message.get("reasoning_content", "")

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

        return LMResponse(
            text=text,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            success=True,
            reasoning_content=reasoning,
        )

    except (socket.timeout, TimeoutError):
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"LM Studio timed out after {timeout}s",
        )

    except urllib.error.URLError as e:
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"LM Studio connection failed: {e.reason}",
        )
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return LMResponse(
            text="",
            model=model,
            prompt_tokens=0,
            completion_tokens=0,
            success=False,
            error=f"Unexpected response format: {e}",
        )
    except Exception as e:
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
    try:
        req = urllib.request.Request(
            f"{LM_STUDIO_BASE}/v1/models",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False
