import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, call, patch
import pytest

from google.genai import errors
from app.core.config import settings
from app.services.llm_client import (
    FALLBACK_MODELS,
    PRIMARY_MODEL,
    call_gemini,
    strip_markdown_fences,
)


def _make_mock_response(text: str = "generated code"):
    resp = MagicMock()
    resp.text = text
    return resp


class MockServerError(errors.ServerError):
    """Mock ServerError matching google.genai.errors.ServerError."""
    def __init__(self, code: int = 503, message: str = "503 UNAVAILABLE: The model is overloaded due to high demand."):
        super().__init__(code, MagicMock(json=lambda: {"error": {"code": code, "message": message, "status": "UNAVAILABLE"}}))
        self.code = code
        self.status_code = code
        self.message = message
        self.status = "UNAVAILABLE"

    def __str__(self):
        return f"{self.code} {self.status}: {self.message}"


class MockClientError(errors.ClientError):
    """Mock ClientError matching google.genai.errors.ClientError."""
    def __init__(self, code: int, message: str, status: str = ""):
        super().__init__(code, MagicMock(json=lambda: {"error": {"code": code, "message": message, "status": status}}))
        self.code = code
        self.status_code = code
        self.message = message
        self.status = status

    def __str__(self):
        return f"{self.code} {self.status}: {self.message}"


def test_candidate_models_definition():
    """Requirement 1: Verify PRIMARY_MODEL and active valid FALLBACK_MODELS."""
    assert PRIMARY_MODEL == getattr(settings, "GEMINI_MODEL", "gemini-3.6-flash")
    assert "gemini-3.5-flash" in FALLBACK_MODELS
    assert len(FALLBACK_MODELS) >= 3


@pytest.mark.anyio
async def test_call_gemini_success_first_candidate(capsys):
    mock_resp = _make_mock_response("print('hello')")

    with patch("app.services.llm_client.client.models.generate_content", return_value=mock_resp) as mock_gen, \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        result = await call_gemini(prompt="test prompt")

        assert result == "print('hello')"
        assert mock_gen.call_count == 1
        assert mock_gen.call_args[1]["model"] == PRIMARY_MODEL
        mock_sleep.assert_not_called()

        captured = capsys.readouterr().out
        assert f"[LLM_CLIENT] Attempting model: {PRIMARY_MODEL}" in captured
        assert f"[LLM_CLIENT] Success on model: {PRIMARY_MODEL}" in captured


@pytest.mark.anyio
async def test_call_gemini_503_immediate_fallback(caplog, capsys):
    """Requirement 2: If 503 occurs, immediately fall back to next model candidate without backoff."""
    err_503 = MockServerError(code=503, message="503 UNAVAILABLE: overloaded")
    mock_resp = _make_mock_response("recovered code")

    first_fallback = FALLBACK_MODELS[0]

    with patch("app.services.llm_client.client.models.generate_content", side_effect=[err_503, mock_resp]) as mock_gen, \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         caplog.at_level(logging.WARNING):

        result = await call_gemini(prompt="test prompt")

        assert result == "recovered code"
        assert mock_gen.call_count == 2
        # First call: PRIMARY_MODEL
        assert mock_gen.call_args_list[0][1]["model"] == PRIMARY_MODEL
        # Second call: First fallback
        assert mock_gen.call_args_list[1][1]["model"] == first_fallback
        # Immediate fallback: NO sleep was executed between 503 and next candidate!
        mock_sleep.assert_not_called()

        # Check required log string
        expected_log = f"Model {PRIMARY_MODEL} overloaded (503). Falling back to next model candidate..."
        assert any(expected_log in record.message for record in caplog.records)

        captured = capsys.readouterr().out
        assert f"[LLM_CLIENT] Attempting model: {PRIMARY_MODEL}" in captured
        assert f"[LLM_CLIENT] Error on model {PRIMARY_MODEL}:" in captured
        assert f"[LLM_CLIENT] Attempting model: {first_fallback}" in captured
        assert f"[LLM_CLIENT] Success on model: {first_fallback}" in captured


@pytest.mark.anyio
async def test_call_gemini_quick_backoff_on_429():
    """Requirement 2: 2 attempts with quick backoff (1s, 2s)."""
    err_429 = MockClientError(code=429, message="Rate limit exceeded", status="RESOURCE_EXHAUSTED")
    mock_resp = _make_mock_response("ok")

    with patch("app.services.llm_client.client.models.generate_content", side_effect=[err_429, mock_resp]) as mock_gen, \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        result = await call_gemini(prompt="test prompt")

        assert result == "ok"
        assert mock_gen.call_count == 2
        assert mock_gen.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert mock_gen.call_args_list[1][1]["model"] == PRIMARY_MODEL
        mock_sleep.assert_awaited_once_with(1)


@pytest.mark.anyio
async def test_call_gemini_multi_tiering_fallback_chain(caplog):
    """Test 503 cascading through multiple candidate models until one succeeds."""
    err_503 = MockServerError(code=503, message="UNAVAILABLE")
    mock_resp = _make_mock_response("final success")

    with patch("app.services.llm_client.client.models.generate_content", side_effect=[err_503, err_503, mock_resp]) as mock_gen, \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         caplog.at_level(logging.WARNING):

        result = await call_gemini(prompt="test")

        assert result == "final success"
        assert mock_gen.call_count == 3
        assert mock_gen.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert mock_gen.call_args_list[1][1]["model"] == FALLBACK_MODELS[0]
        assert mock_gen.call_args_list[2][1]["model"] == FALLBACK_MODELS[1]
        mock_sleep.assert_not_called()

        assert any(f"Model {PRIMARY_MODEL} overloaded (503). Falling back to next model candidate..." in r.message for r in caplog.records)
        assert any(f"Model {FALLBACK_MODELS[0]} overloaded (503). Falling back to next model candidate..." in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_call_gemini_all_models_fail(capsys):
    """Requirement 2: Only return None or raise if all candidate models fail."""
    err_503 = MockServerError(code=503, message="All down")
    total_candidates = 1 + len(FALLBACK_MODELS)

    with patch("app.services.llm_client.client.models.generate_content", side_effect=err_503) as mock_gen:
        with pytest.raises(RuntimeError) as exc_info:
            await call_gemini(prompt="test")

        assert "All candidate models failed" in str(exc_info.value) or "All down" in str(exc_info.value)
        assert mock_gen.call_count == total_candidates

        captured = capsys.readouterr().out
        assert "[LLM_CLIENT] All candidate models failed." in captured


def test_strip_markdown_fences():
    """Requirement 3: Ensure markdown fences are returned cleanly as plain string."""
    py_code = "```python\ndef add(a, b):\n    return a + b\n```"
    assert strip_markdown_fences(py_code) == "def add(a, b):\n    return a + b"

    raw_fence = "```\nhello plain text\n```"
    assert strip_markdown_fences(raw_fence) == "hello plain text"

    inline_fence = "```python def add(a, b): return a + b```"
    assert strip_markdown_fences(inline_fence) == "def add(a, b): return a + b"

    plain_text = "def add(a, b):\n    return a + b"
    assert strip_markdown_fences(plain_text) == plain_text

    empty = ""
    assert strip_markdown_fences(empty) == ""


@pytest.mark.anyio
async def test_call_gemini_cleans_markdown_fences_in_output():
    """Requirement 3: call_gemini automatically strips fences."""
    fenced_output = "```python\ndef calculate_discount(price):\n    return price * 0.9\n```"
    mock_resp = _make_mock_response(fenced_output)

    with patch("app.services.llm_client.client.models.generate_content", return_value=mock_resp):
        result = await call_gemini(prompt="write code")
        assert result == "def calculate_discount(price):\n    return price * 0.9"
