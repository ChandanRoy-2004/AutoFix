import logging
import re

from app.core.config import settings
from app.core.prompt_templates import HEALER_SYSTEM_PROMPT
from app.services.llm_client import call_gemini

logger = logging.getLogger(__name__)


def _clean_code_blocks(raw_text: str) -> str:
    """Strip markdown code blocks (e.g. ```python and ```) to return only executable Python code."""
    if not raw_text:
        return ""

    text = raw_text.strip()

    # Match fenced code blocks starting with ```python, ```py, or ```
    fence_pattern = r"^```(?:python|py)?\s*\n([\s\S]*?)\n```\s*$"
    match = re.search(fence_pattern, text, re.MULTILINE)
    if match:
        return match.group(1).strip()

    # Search for fenced code blocks anywhere in text with surrounding text
    general_pattern = r"```(?:python|py)?\s*\n([\s\S]*?)\n```"
    matches = list(re.finditer(general_pattern, text))
    if matches:
        longest = max(matches, key=lambda m: len(m.group(1)))
        return longest.group(1).strip()

    # Handle unclosed opening fence
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()

    return text


async def generate_code_patch(
    buggy_code: str,
    requirements: str,
    error_logs: str,
) -> str:
    """Analyze test failures and return a repaired, bug-free implementation of the target code."""
    user_prompt = f"""### Functional Requirements & Expected Behavior:
{requirements}

### Original Target Source Code (`target.py`):
```python
{buggy_code}
```

### Pytest Error Logs & Failure Traceback:
```text
{error_logs}
```

### Task:
Diagnose the root cause of the failures reported in the traceback and write the complete, corrected implementation of `target.py`.
Constraints:
1. Output the COMPLETE corrected Python source code.
2. Fix all bugs, runtime errors, and unhandled edge cases causing the tests to fail.
3. Strictly preserve all existing function signatures, class names, method interfaces, parameter types, and return types.
4. Return ONLY valid, executable Python source code without commentary or conversational text.
"""

    logger.info("Generating code patch with Healer Agent using model %s...", settings.PRIMARY_MODEL)
    raw_response = await call_gemini(
        prompt=user_prompt,
        system_instruction=HEALER_SYSTEM_PROMPT,
        model=settings.PRIMARY_MODEL,
    )

    clean_code = _clean_code_blocks(raw_response)
    return clean_code.strip()
