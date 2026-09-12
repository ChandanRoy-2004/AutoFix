import asyncio
import logging
import os
import re
from typing import Optional

from google import genai
from google.genai import errors, types

from app.core.config import settings

logger = logging.getLogger(__name__)

_client = None


def get_client() -> genai.Client:
    global _client
    if _client is None:
        api_key = (
            getattr(settings, "GOOGLE_API_KEY", None)
            or getattr(settings, "GEMINI_API_KEY", None)
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
        )
        if not api_key:
            raise ValueError("Google API key is not configured in settings or environment.")
        _client = genai.Client(api_key=api_key)
    return _client


# Alias for backward compatibility
get_genai_client = get_client

PRIMARY_MODEL = getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
# Valid models for Google GenAI SDK (verified working on API)
FALLBACK_MODELS = [
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.7-flash",
    "gemini-flash-latest",
]
QUICK_BACKOFF_DELAYS = [1, 2]


def strip_markdown_fences(text: str) -> str:
    """Ensure markdown fences are returned cleanly as plain string."""
    if not text:
        return ""

    s = text.strip()

    # Match standard multiline fenced code block: ```lang\n...content...\n```
    match = re.match(r"^```(?:[a-zA-Z0-9_+-]+)?\s*\n([\s\S]*?)\n```\s*$", s)
    if match:
        return match.group(1).strip()

    # Match single-line / inline fenced code block: ```lang ... ```
    match = re.match(r"^```(?:[a-zA-Z0-9_+-]+)?\s*([\s\S]*?)\s*```$", s)
    if match:
        return match.group(1).strip()

    # Handle unclosed opening fence or trailing closing fence
    if s.startswith("```"):
        lines = s.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    return s


def _is_503_overloaded(e: Exception) -> bool:
    """Check if exception represents a 503 error (high demand / UNAVAILABLE)."""
    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    status = getattr(e, "status", None)
    err_str = str(e).lower()
    msg = (getattr(e, "message", None) or "").lower()

    if code == 503:
        return True
    if isinstance(status, str) and status.upper() == "UNAVAILABLE":
        return True
    if isinstance(e, errors.ServerError) and (code == 503 or "503" in err_str):
        return True
    if "503" in err_str or "503" in msg:
        return True
    if "high demand" in err_str or "high demand" in msg:
        return True
    if "unavailable" in err_str or "unavailable" in msg:
        return True
    response = getattr(e, "response", None)
    if response is not None and getattr(response, "status_code", None) == 503:
        return True
    return False


def _is_transient_error(e: Exception) -> bool:
    """Check if exception represents a transient error eligible for retry."""
    if _is_503_overloaded(e):
        return True

    code = getattr(e, "status_code", None) or getattr(e, "code", None)
    status = getattr(e, "status", None)
    err_str = str(e).lower()
    msg = (getattr(e, "message", None) or "").lower()

    if isinstance(e, errors.ServerError):
        return True

    # 429 RESOURCE_EXHAUSTED / rate limit checks
    if code == 429 or (isinstance(status, str) and status.upper() in ("RESOURCE_EXHAUSTED", "TOO_MANY_REQUESTS")):
        return True
    if "resource_exhausted" in msg or "too many requests" in msg or "resource_exhausted" in err_str or "too many requests" in err_str or "429" in err_str:
        return True

    response = getattr(e, "response", None)
    if response is not None and getattr(response, "status_code", None) in (503, 429):
        return True

    return False


async def call_gemini(
    prompt: str,
    system_instruction: str = "",
    model: Optional[str] = None,
    max_retries: int = 2,
) -> Optional[str]:
    """Execute an asynchronous call to Google Gemini LLM with multi-model tiering.

    Requirements:
    1. Iterate through models: [PRIMARY_MODEL] + [m for m in FALLBACK_MODELS if m != PRIMARY_MODEL]
    2. For each model, attempt with quick backoff (2 attempts: 1s, 2s).
    3. If a model throws a 503 (high demand / UNAVAILABLE), log:
       f"Model {model_name} overloaded (503). Falling back to next model candidate..."
       and immediately attempt the next model in the list.
    4. Print explicit progress at every stage:
       - print(f"[LLM_CLIENT] Attempting model: {model_name}")
       - print(f"[LLM_CLIENT] Success on model: {model_name}")
       - print(f"[LLM_CLIENT] Error on model {model_name}: {err}")
    5. Strip markdown fences and return clean plain string.
    6. Only return None or raise if all candidate models fail, printing:
       print("[LLM_CLIENT] All candidate models failed.")
    """
    primary = model or getattr(settings, "GEMINI_MODEL", None) or PRIMARY_MODEL
    candidate_models = [primary] + [m for m in FALLBACK_MODELS if m != primary]

    get_client()

    config = types.GenerateContentConfig(
        system_instruction=system_instruction if system_instruction else None,
        temperature=0.1,
    )

    last_error: Optional[Exception] = None

    for model_name in candidate_models:
        for attempt in range(max_retries):
            print(f"[LLM_CLIENT] Attempting model: {model_name}")
            try:
                response = await asyncio.to_thread(
                    get_client().models.generate_content,
                    model=model_name,
                    contents=prompt,
                    config=config,
                )

                if response and response.text:
                    print(f"[LLM_CLIENT] Success on model: {model_name}")
                    return strip_markdown_fences(response.text)

                logger.warning("Empty response received from Gemini model %s", model_name)

            except Exception as e:
                err = e
                last_error = e
                print(f"[LLM_CLIENT] Error on model {model_name}: {err}")
                msg = getattr(e, "message", None)
                err_msg = msg if isinstance(msg, str) and msg else str(e)

                # 503 (high demand / UNAVAILABLE): immediately attempt the next model candidate
                if _is_503_overloaded(e):
                    logger.warning(
                        f"Model {model_name} overloaded (503). Falling back to next model candidate..."
                    )
                    break

                # Non-retryable error (e.g. 400, 401, 404): log immediately and move to next candidate without backoff
                if not _is_transient_error(e):
                    logger.error(
                        "Non-retryable error calling Gemini model %s: %s",
                        model_name,
                        err_msg,
                    )
                    break

                # Transient error (e.g. 429): quick backoff (1s, 2s)
                delay = QUICK_BACKOFF_DELAYS[attempt] if attempt < len(QUICK_BACKOFF_DELAYS) else QUICK_BACKOFF_DELAYS[-1] * 2
                logger.warning(
                    "Call to model %s encountered transient error (attempt %d/%d): %s. Retrying in %ds...",
                    model_name,
                    attempt + 1,
                    max_retries,
                    err_msg,
                    delay,
                )
                await asyncio.sleep(delay)

    # All candidate models failed
    print("[LLM_CLIENT] All candidate models failed.")
    err_msg = (getattr(last_error, "message", None) or str(last_error)) if last_error else "All candidate models failed"
    logger.error("[LLM_CLIENT] All candidate models failed: %s", err_msg)
    if isinstance(last_error, errors.APIError):
        raise RuntimeError(f"Gemini API Error: {err_msg}") from last_error
    if last_error:
        raise RuntimeError(f"LLM generation failed: {str(last_error)}") from last_error

    return None
