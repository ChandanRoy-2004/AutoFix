import inspect
from pathlib import Path
import pytest

from app.core import config, prompt_templates
from app.models import schemas
from app.services import sandboxes
from app.services.orchestrator import run_healing_pipeline, run_repo_healing_pipeline
from app.services.repo_analyzer import RepoAnalyzer
from app.services.sandboxes import BaseSandboxAdapter, PythonAdapter


def test_sandboxes_exports_only_base_adapter_and_python_adapter():
    """Requirement 1: Verify sandboxes exports only BaseSandboxAdapter and PythonAdapter."""
    assert hasattr(sandboxes, "BaseSandboxAdapter")
    assert hasattr(sandboxes, "PythonAdapter")
    assert sandboxes.__all__ == ["BaseSandboxAdapter", "PythonAdapter"]

    # Verify non-Python adapter classes are eliminated
    assert not hasattr(sandboxes, "CSharpSandbox")
    assert not hasattr(sandboxes, "JavaSandbox")
    assert not hasattr(sandboxes, "CSharpAdapter")
    assert not hasattr(sandboxes, "JavaAdapter")
    assert not hasattr(sandboxes, "NodeAdapter")
    assert not hasattr(sandboxes, "GoAdapter")


def test_python_adapter_runs_pytest_with_pythonpath(tmp_path: Path):
    """Requirement 1: Verify PythonAdapter sets PYTHONPATH to repository directory."""
    adapter = PythonAdapter()
    workspace = tmp_path / "repo"
    workspace.mkdir()

    from unittest.mock import patch
    with patch.object(adapter, "_execute_command") as mock_exec:
        mock_exec.return_value = (True, "1 passed")

        passed, output = adapter.run_tests(workspace)
        assert passed is True
        mock_exec.assert_called_once()
        cmd = mock_exec.call_args[0][0]
        kwargs = mock_exec.call_args[1]

        assert "-m" in cmd
        assert "pytest" in cmd
        assert kwargs["env"]["PYTHONPATH"] == str(workspace.resolve())


def test_repo_analyzer_exclusively_scans_python_files(tmp_path: Path):
    """Requirement 3: Verify RepoAnalyzer uses Python AST and focuses strictly on .py files."""
    analyzer = RepoAnalyzer()
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()

    # Create Python module and dependent file
    models_dir = repo_dir / "models"
    models_dir.mkdir()
    user_py = models_dir / "user.py"
    user_py.write_text("class User:\n    pass\n", encoding="utf-8")

    main_py = repo_dir / "main.py"
    main_py.write_text("from models.user import User\n\ndef run(): return User()\n", encoding="utf-8")

    # Create non-Python files that should be completely ignored
    (repo_dir / "Service.cs").write_text("using System; class Service {}", encoding="utf-8")
    (repo_dir / "App.java").write_text("package com.app; class App {}", encoding="utf-8")
    (repo_dir / "index.ts").write_text("import { x } from './foo';", encoding="utf-8")
    (repo_dir / "server.go").write_text("package main", encoding="utf-8")

    # Verify non-Python scanner methods were removed
    assert not hasattr(analyzer, "scan_csharp_dependencies")
    assert not hasattr(analyzer, "scan_java_dependencies")

    # Build dependency graph
    graph = analyzer.build_dependency_graph(repo_dir)

    # Graph should contain ONLY Python files
    assert "main.py" in graph
    assert "models/user.py" in graph
    assert "Service.cs" not in graph
    assert "App.java" not in graph
    assert "index.ts" not in graph
    assert "server.go" not in graph

    # Verify AST dependency link was extracted
    assert "models/user.py" in graph["main.py"]

    # Test context extraction
    context = analyzer.extract_relevant_context(repo_dir, "main.py")
    assert "main.py" in context
    assert "models/user.py" in context
    assert "Service.cs" not in context
    assert "App.java" not in context


def test_prompt_templates_pruned_to_idiomatic_python():
    """Requirement 4: Verify prompt templates focus exclusively on Python 3.12+ and pytest."""
    # Check non-Python prompts are removed
    assert not hasattr(prompt_templates, "CSHARP_TEST_ENGINEER_PROMPT")
    assert not hasattr(prompt_templates, "JAVA_TEST_ENGINEER_PROMPT")
    assert not hasattr(prompt_templates, "CSHARP_HEALER_PROMPT")
    assert not hasattr(prompt_templates, "JAVA_HEALER_PROMPT")

    # Check Python prompt content
    assert "Python 3.12+" in prompt_templates.PYTHON_TEST_ENGINEER_PROMPT
    assert "pytest" in prompt_templates.PYTHON_TEST_ENGINEER_PROMPT
    assert "Python 3.12+" in prompt_templates.PYTHON_HEALER_PROMPT
    assert "type annotations" in prompt_templates.PYTHON_HEALER_PROMPT

    # Function helpers default to Python
    assert prompt_templates.get_test_engineer_prompt() == prompt_templates.PYTHON_TEST_ENGINEER_PROMPT
    assert prompt_templates.get_healer_prompt() == prompt_templates.PYTHON_HEALER_PROMPT


def test_schemas_and_config_default_to_python():
    """Requirement 4: Verify schemas and config default exclusively to Python."""
    req = schemas.HealRequest(
        buggy_code="def f(): pass",
        requirements="implement f",
    )
    assert req.language == "python"

    resp = schemas.HealResponse(
        success=True,
        iterations_used=1,
        final_code="def f(): return True",
        generated_tests="",
    )
    assert resp.language == "python"

    assert config.settings.SUPPORTED_LANGUAGES == ["python"]
