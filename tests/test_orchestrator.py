from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.models.schemas import HealRequest
from app.services.orchestrator import (
    clean_code_fences,
    run_healing_pipeline,
    run_repo_healing_pipeline,
)


def test_clean_code_fences():
    # Python fence
    py_code = "```python\ndef add(a, b):\n    return a + b\n```"
    assert clean_code_fences(py_code) == "def add(a, b):\n    return a + b"

    # C# fence
    cs_code = "```csharp\npublic class Calculator {\n}\n```"
    assert clean_code_fences(cs_code) == "public class Calculator {\n}"

    # Java fence
    java_code = "```java\npublic class App {\n}\n```"
    assert clean_code_fences(java_code) == "public class App {\n}"

    # Plain fence
    plain_fence = "```\nprint('hello')\n```"
    assert clean_code_fences(plain_fence) == "print('hello')"

    # Raw text without fences
    raw_code = "x = 10\ny = 20"
    assert clean_code_fences(raw_code) == "x = 10\ny = 20"

    # Empty string
    assert clean_code_fences("") == ""


@pytest.mark.anyio
async def test_run_healing_pipeline_first_pass_success(tmp_path: Path):
    req = HealRequest(
        language="python",
        buggy_code="def add(a, b): return a + b",
        requirements="Function adds two numbers",
        file_path="target.py",
    )

    with patch.object(settings, "WORKSPACE_DIR", tmp_path), \
         patch("app.services.orchestrator.call_gemini", new_callable=AsyncMock) as mock_gemini, \
         patch("app.services.orchestrator.PythonAdapter") as mock_adapter_cls:

        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = (True, "1 passed in 0.01s")
        mock_adapter_cls.return_value = mock_sandbox

        mock_gemini.return_value = "```python\ndef test_add():\n    from target import add\n    assert add(1, 2) == 3\n```"

        response = await run_healing_pipeline(req)

        assert response.success is True
        assert response.iterations_used == 0
        assert response.language == "python"
        assert len(response.logs) > 0


@pytest.mark.anyio
async def test_run_healing_pipeline_self_healing_success(tmp_path: Path):
    req = HealRequest(
        language="python",
        buggy_code="def add(a, b): return a - b",
        requirements="Function adds two numbers",
        file_path="target.py",
    )

    with patch.object(settings, "WORKSPACE_DIR", tmp_path), \
         patch("app.services.orchestrator.call_gemini", new_callable=AsyncMock) as mock_gemini, \
         patch("app.services.orchestrator.PythonAdapter") as mock_adapter_cls:

        mock_sandbox = MagicMock()
        # First run fails, second run passes
        mock_sandbox.run_tests.side_effect = [
            (False, "FAILED test_add - AssertionError: -1 != 3"),
            (True, "1 passed in 0.02s"),
        ]
        mock_adapter_cls.return_value = mock_sandbox

        # 1st LLM call is test generation, 2nd LLM call is healer
        mock_gemini.side_effect = [
            "```python\ndef test_add():\n    from target import add\n    assert add(1, 2) == 3\n```",
            "```python\ndef add(a, b):\n    return a + b\n```",
        ]

        response = await run_healing_pipeline(req)

        assert response.success is True
        assert response.iterations_used == 1
        assert "def add(a, b):" in response.final_code
        assert len(response.patches) == 1
        assert response.patches[0].original_content == "def add(a, b): return a - b"


@pytest.mark.anyio
async def test_run_repo_healing_pipeline(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    failing_file = repo_dir / "service.py"
    failing_file.write_text("def calc(): return 0", encoding="utf-8")

    with patch("app.services.orchestrator.call_gemini", new_callable=AsyncMock) as mock_gemini, \
         patch("app.services.orchestrator.PythonAdapter") as mock_adapter_cls, \
         patch("app.services.orchestrator.RepoAnalyzer.extract_relevant_context") as mock_extract:

        mock_extract.return_value = {"service.py": "def calc(): return 0"}

        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = (True, "All repo tests passed!")
        mock_adapter_cls.return_value = mock_sandbox

        mock_gemini.return_value = "```python\ndef calc():\n    return 42\n```"

        response = await run_repo_healing_pipeline(
            repo_dir=repo_dir,
            language="python",
            failing_file="service.py",
            failing_logs="FAILED test_calc: expected 42, got 0",
        )

        assert response.success is True
        assert response.iterations_used == 1
        assert "return 42" in response.final_code
        assert len(response.patches) == 1


@pytest.mark.anyio
async def test_run_repo_healing_pipeline_targets_test_order_processor(tmp_path: Path):
    """Verify run_repo_healing_pipeline targets test_order_processor.py when present in repo."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "order_processor.py").write_text("def process_order(): pass", encoding="utf-8")
    (repo_dir / "test_order_processor.py").write_text("def test_proc(): pass", encoding="utf-8")

    with patch("app.services.orchestrator.call_gemini", new_callable=AsyncMock) as mock_gemini, \
         patch("app.services.orchestrator.PythonAdapter") as mock_adapter_cls, \
         patch("app.services.orchestrator.RepoAnalyzer.extract_relevant_context") as mock_extract:

        mock_extract.return_value = {"order_processor.py": "def process_order(): pass"}
        mock_sandbox = MagicMock()
        mock_sandbox.run_tests.return_value = (True, "3 passed in 0.01s")
        mock_adapter_cls.return_value = mock_sandbox
        mock_gemini.return_value = "```python\ndef process_order(): return {'status': 'ok'}\n```"

        response = await run_repo_healing_pipeline(
            repo_dir=repo_dir,
            language="python",
            failing_file="order_processor.py",
            failing_logs="IndexError: list index out of range",
        )

        assert response.success is True
        mock_sandbox.run_tests.assert_called_once_with(
            repo_dir.resolve(),
            timeout=30,
            test_file="test_order_processor.py",
        )


def test_python_sandbox_pytest_discovery_and_overrides(tmp_path: Path):
    """Test PythonSandbox overrides restrictive testpaths and sets PYTHONPATH to workspace."""
    from app.services.sandboxes.adapters import PythonSandbox

    sandbox = PythonSandbox()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "test_order_processor.py").write_text("def test_ok(): assert True\n", encoding="utf-8")

    with patch.object(sandbox, "_execute_command") as mock_exec:
        mock_exec.return_value = (True, "1 passed")

        passed, output = sandbox.run_tests(workspace)
        assert passed is True
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][0]
        kwargs = mock_exec.call_args[1]

        # Ensure test_order_processor.py and testpaths override are in cmd
        assert "test_order_processor.py" in cmd
        assert "-o" in cmd
        assert "testpaths=." in cmd
        assert kwargs["env"]["PYTHONPATH"] == str(workspace.resolve())


@pytest.mark.anyio
async def test_python_sandbox_create_subprocess_exec(tmp_path: Path):
    """Test PythonSandbox.create_subprocess_exec executes with correct cmd and env."""
    from app.services.sandboxes.adapters import PythonSandbox, PythonAdapter
    assert PythonAdapter is PythonSandbox

    sandbox = PythonSandbox()
    workspace = tmp_path / "repo"
    workspace.mkdir()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"test passed", b""))
        mock_proc.returncode = 0
        mock_exec.return_value = mock_proc

        passed, output = await sandbox.create_subprocess_exec(
            repo_dir=workspace,
            test_file="tests/test_event_formatter.py",
        )

        assert passed is True
        assert "test passed" in output
        mock_exec.assert_called_once()
        cmd_args = mock_exec.call_args[0]
        kwargs = mock_exec.call_args[1]

        assert "tests/test_event_formatter.py" in cmd_args
        assert "-v" in cmd_args
        assert kwargs["cwd"] == str(workspace.resolve())
        assert kwargs["env"]["PYTHONPATH"] == str(workspace.resolve())


def test_python_sandbox_targets_event_formatter(tmp_path: Path):
    """Test PythonSandbox defaults to tests/test_event_formatter.py when event_formatter is target."""
    from app.services.sandboxes.adapters import PythonSandbox

    sandbox = PythonSandbox()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch.object(sandbox, "_execute_command") as mock_exec:
        mock_exec.return_value = (True, "2 passed")

        passed, output = sandbox.run_tests(workspace, test_file="event_formatter.py")
        assert passed is True
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][0]
        kwargs = mock_exec.call_args[1]

        assert "tests/test_event_formatter.py" in cmd
        assert "-v" in cmd
        assert kwargs["env"]["PYTHONPATH"] == str(workspace.resolve())

