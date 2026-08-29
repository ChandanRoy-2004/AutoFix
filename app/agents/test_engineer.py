import logging
import re

from app.core.config import settings
from app.core.prompt_templates import TEST_ENGINEER_SYSTEM_PROMPT
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


async def generate_test_suite(target_code: str, requirements: str) -> str:
    """Generate a comprehensive pytest suite based on target source code and requirements."""
    user_prompt = f"""### Functional Requirements & Specifications:
{requirements}

### Target Source Code (`target.py`):
```python
{target_code}
```

### Task:
Write a comprehensive pytest test suite to validate the target code against the requirements above.
Constraints:
1. You MUST import the target code using: `from target import *`.
2. Cover standard behavior, boundary conditions, edge cases, and expected exceptions with pytest.
3. Return ONLY valid Python test code without commentary.
"""

    logger.info("Generating test suite with Test Engineer persona using model %s...", settings.FAST_MODEL)
    raw_response = await call_gemini(
        prompt=user_prompt,
        system_instruction=TEST_ENGINEER_SYSTEM_PROMPT,
        model=settings.FAST_MODEL,
    )

    clean_code = _clean_code_blocks(raw_response)

    # Ensure required target import exists
    if "from target import" not in clean_code and "import target" not in clean_code:
        clean_code = f"from target import *\n\n{clean_code}"

    # Ensure pytest import exists
    if "import pytest" not in clean_code:
        clean_code = f"import pytest\n{clean_code}"

    return clean_code.strip()
