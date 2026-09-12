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
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.services.llm_client.get_client", return_value=mock_client), \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        result = await call_gemini(prompt="test prompt")

        assert result == "print('hello')"
        assert mock_client.models.generate_content.call_count == 1
        assert mock_client.models.generate_content.call_args[1]["model"] == PRIMARY_MODEL
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

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [err_503, mock_resp]

    with patch("app.services.llm_client.get_client", return_value=mock_client), \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         caplog.at_level(logging.WARNING):

        result = await call_gemini(prompt="test prompt")

        assert result == "recovered code"
        assert mock_client.models.generate_content.call_count == 2
        # First call: PRIMARY_MODEL
        assert mock_client.models.generate_content.call_args_list[0][1]["model"] == PRIMARY_MODEL
        # Second call: First fallback
        assert mock_client.models.generate_content.call_args_list[1][1]["model"] == first_fallback
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

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [err_429, mock_resp]

    with patch("app.services.llm_client.get_client", return_value=mock_client), \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:

        result = await call_gemini(prompt="test prompt")

        assert result == "ok"
        assert mock_client.models.generate_content.call_count == 2
        assert mock_client.models.generate_content.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert mock_client.models.generate_content.call_args_list[1][1]["model"] == PRIMARY_MODEL
        mock_sleep.assert_awaited_once_with(1)


@pytest.mark.anyio
async def test_call_gemini_multi_tiering_fallback_chain(caplog):
    """Test 503 cascading through multiple candidate models until one succeeds."""
    err_503 = MockServerError(code=503, message="UNAVAILABLE")
    mock_resp = _make_mock_response("final success")

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [err_503, err_503, mock_resp]

    with patch("app.services.llm_client.get_client", return_value=mock_client), \
         patch("app.services.llm_client.asyncio.sleep", new_callable=AsyncMock) as mock_sleep, \
         caplog.at_level(logging.WARNING):

        result = await call_gemini(prompt="test")

        assert result == "final success"
        assert mock_client.models.generate_content.call_count == 3
        assert mock_client.models.generate_content.call_args_list[0][1]["model"] == PRIMARY_MODEL
        assert mock_client.models.generate_content.call_args_list[1][1]["model"] == FALLBACK_MODELS[0]
        assert mock_client.models.generate_content.call_args_list[2][1]["model"] == FALLBACK_MODELS[1]
        mock_sleep.assert_not_called()

        assert any(f"Model {PRIMARY_MODEL} overloaded (503). Falling back to next model candidate..." in r.message for r in caplog.records)
        assert any(f"Model {FALLBACK_MODELS[0]} overloaded (503). Falling back to next model candidate..." in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_call_gemini_all_models_fail(capsys):
    """Requirement 2: Only return None or raise if all candidate models fail."""
    err_503 = MockServerError(code=503, message="All down")
    total_candidates = 1 + len(FALLBACK_MODELS)

    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = err_503

    with patch("app.services.llm_client.get_client", return_value=mock_client):
        with pytest.raises(RuntimeError) as exc_info:
            await call_gemini(prompt="test")

        assert "All candidate models failed" in str(exc_info.value) or "All down" in str(exc_info.value)
        assert mock_client.models.generate_content.call_count == total_candidates

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
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_resp

    with patch("app.services.llm_client.get_client", return_value=mock_client):
        result = await call_gemini(prompt="write code")
        assert result == "def calculate_discount(price):\n    return price * 0.9"


def test_get_genai_client_lazy_initialization():
    """Test get_client and get_genai_client initialize with configured API key and cache instance."""
    from app.services.llm_client import get_client, get_genai_client
    import app.services.llm_client as llm_mod

    # Reset cached client
    llm_mod._client = None

    with patch("google.genai.Client") as mock_client_cls, \
         patch.object(settings, "GOOGLE_API_KEY", "test-api-key-123"):
        mock_instance = MagicMock()
        mock_client_cls.return_value = mock_instance

        client1 = get_client()
        assert client1 == mock_instance
        mock_client_cls.assert_called_once_with(api_key="test-api-key-123")

        # Second call returns cached client without re-instantiating
        client2 = get_genai_client()
        assert client2 == mock_instance
        assert mock_client_cls.call_count == 1


@pytest.mark.anyio
async def test_call_gemini_missing_api_key_raises_inside_call():
    """Verify empty API key raises explicit error only inside call_gemini, never on module import."""
    import app.services.llm_client as llm_mod
    llm_mod._client = None

    mock_settings = MagicMock()
    mock_settings.GOOGLE_API_KEY = None
    mock_settings.GEMINI_API_KEY = None

    with patch("app.services.llm_client.settings", mock_settings), \
         patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError) as exc_info:
            await call_gemini(prompt="hello")

        assert "Google API key is not configured in settings or environment." in str(exc_info.value)


def test_app_services_init_avoids_client_import():
    """Verify app.services does not import or export client at module load."""
    import app.services as srv
    assert not hasattr(srv, "client")
    assert "client" not in srv.__all__
    assert hasattr(srv, "get_client")
    assert hasattr(srv, "get_genai_client")

