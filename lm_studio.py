"""
LM Studio HTTP client — stdlib-only, OpenAI-compatible endpoint.

All communication with LM Studio goes through this module.
Uses urllib (no openai package required).
"""

import json
import urllib.request
import urllib.error
from dataclasses import dataclass


LM_STUDIO_BASE  = "http://127.0.0.1:1234"
DRAFTER_MODEL   = "granite-4.0-tiny-h"     # fast MoE model
VALIDATOR_MODEL = "devstral-small-2"        # deep reasoning model
TIMEOUT_DRAFTER   = 30    # seconds — Granite is fast
TIMEOUT_VALIDATOR = 120   # seconds — Devstral is thorough


@dataclass
class LMResponse:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    success: bool
    error: str = ""


def call_lm_studio(
    model: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int,
    temperature: float = 0.15,
    max_tokens: int = 2048,
) -> LMResponse:
    """Single call to the LM Studio OpenAI-compatible endpoint."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "stream":      False,
    }

    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        f"{LM_STUDIO_BASE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        choice = data["choices"][0]["message"]["content"]
        usage  = data.get("usage", {})
        return LMResponse(
            text=choice,
            model=model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            success=True,
        )

    except urllib.error.URLError as e:
        return LMResponse(
            text="", model=model,
            prompt_tokens=0, completion_tokens=0,
            success=False,
            error=f"LM Studio connection failed: {e.reason}",
        )
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return LMResponse(
            text="", model=model,
            prompt_tokens=0, completion_tokens=0,
            success=False,
            error=f"Unexpected response format: {e}",
        )
    except Exception as e:
        return LMResponse(
            text="", model=model,
            prompt_tokens=0, completion_tokens=0,
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
