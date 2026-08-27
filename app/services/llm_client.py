import asyncio
import logging
from typing import Optional

from google import genai
from google.genai import errors, types

from app.core.config import settings

logger = logging.getLogger(__name__)

# Initialize Google GenAI client
client = genai.Client(api_key=settings.GOOGLE_API_KEY)


async def call_gemini(
    prompt: str,
    system_instruction: str = "",
    model: Optional[str] = None,
) -> str:
    """Execute an asynchronous call to Google Gemini LLM."""
    target_model = model or settings.PRIMARY_MODEL

    config = types.GenerateContentConfig(
        system_instruction=system_instruction if system_instruction else None,
        temperature=0.1,
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=target_model,
            contents=prompt,
            config=config,
        )

        if not response or not response.text:
            logger.warning("Empty response received from Gemini model %s", target_model)
            return ""

        return response.text.strip()

    except errors.APIError as e:
        logger.error("Gemini API error (%s): %s", target_model, e.message, exc_info=True)
        raise RuntimeError(f"Gemini API Error: {e.message}") from e
    except Exception as e:
        logger.error("Unexpected error calling Gemini model %s: %s", target_model, str(e), exc_info=True)
        raise RuntimeError(f"LLM generation failed: {str(e)}") from e
