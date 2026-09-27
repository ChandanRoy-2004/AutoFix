import pytest

from app.models.schemas import FilePatch, HealResponse
from app.services.github_service import GitHubService, build_healing_audit_report


def test_build_healing_audit_report_with_kwargs():
    """Verify build_healing_audit_report with explicit keyword arguments."""
    report = build_healing_audit_report(
        status="Verified Passing ✅",
        iteration=2,
        max_iterations=5,
        failing_file="app/utils/event_formatter.py",
        model_name="gemini-3.6-flash",
        formatted_ast_tree="- `app/utils/event_formatter.py`\n- `tests/test_event_formatter.py`",
        test_rows="| `tests/test_event_formatter.py` | Failed ❌ | Passed ✅ |",
        patched_code="def sanitize(): pass",
        language="python",
    )

    expected_elements = [
        "## 🤖 AutoFix Autonomous Healing Report",
        "| Metric | Status |",
        "| :--- | :--- |",
        "| **Status** | Verified Passing ✅ |",
        "| **Iterations Required** | `2 / 5` |",
        "| **Target Module** | `app/utils/event_formatter.py` |",
        "| **LLM Engine** | `gemini-3.6-flash` |",
        "### 🔍 AST Dependency Scope",
        "The following interdependent modules were extracted and analyzed to preserve system contracts:",
        "- `app/utils/event_formatter.py`",
        "- `tests/test_event_formatter.py`",
        "### 🧪 Test Verification Suite",
        "| Test Target | Initial Run | Sandbox Verification |",
        "| :--- | :---: | :---: |",
        "| `tests/test_event_formatter.py` | Failed ❌ | Passed ✅ |",
        "<details>",
        "<summary><b>View Applied Code Patch</b></summary>",
        "```python",
        "def sanitize(): pass",
        "```",
        "</details>",
    ]

    for element in expected_elements:
        assert element in report


def test_build_healing_audit_report_from_heal_response():
    """Verify build_healing_audit_report derives attributes from HealResponse."""
    patch = FilePatch(
        file_path="service.py",
        original_content="def run(): return False",
        patched_content="def run(): return True",
    )
    heal_resp = HealResponse(
        success=True,
        iterations_used=1,
        final_code="def run(): return True",
        generated_tests="",
        language="python",
        patches=[patch],
        failing_file="service.py",
        model_name="gemini-3.6-flash",
        max_iterations=3,
        ast_dependencies=["service.py", "tests/test_service.py"],
        test_target="tests/test_service.py",
    )

    report = build_healing_audit_report(heal_resp)

    assert "## 🤖 AutoFix Autonomous Healing Report" in report
    assert "| **Status** | Verified Passing ✅ |" in report
    assert "| **Iterations Required** | `1 / 3` |" in report
    assert "| **Target Module** | `service.py` |" in report
    assert "| **LLM Engine** | `gemini-3.6-flash` |" in report
    assert "- `service.py`" in report
    assert "- `tests/test_service.py`" in report
    assert "| `tests/test_service.py` | Failed ❌ | Passed ✅ |" in report
    assert "def run(): return True" in report
    assert "<details>" in report
    assert "</details>" in report


def test_build_healing_audit_report_failure_status():
    """Verify report shows Failed ❌ when healing was unsuccessful."""
    heal_resp = HealResponse(
        success=False,
        iterations_used=3,
        final_code="def broken(): pass",
        generated_tests="",
        language="python",
        failing_file="broken.py",
        max_iterations=3,
    )

    report = build_healing_audit_report(heal_resp)

    assert "| **Status** | Failed ❌ |" in report
    assert "| **Iterations Required** | `3 / 3` |" in report
    assert "Failed ❌" in report


def test_github_service_delegates_to_audit_report():
    """Verify GitHubService.format_pr_comment and build_healing_audit_report methods."""
    heal_resp = HealResponse(
        success=True,
        iterations_used=1,
        final_code="x = 1",
        generated_tests="",
        failing_file="app.py",
    )

    report1 = GitHubService.format_pr_comment(heal_resp)
    report2 = GitHubService.build_healing_audit_report(heal_resp)

    assert report1 == report2
    assert "## 🤖 AutoFix Autonomous Healing Report" in report1


def test_build_healing_audit_report_with_failure_logs():
    """Verify report includes truncated_failure_logs collapsible section when logs are provided."""
    long_traceback = "\n".join([f"Traceback line {i}: error details" for i in range(100)])
    report = build_healing_audit_report(
        status="Verified Passing ✅",
        iteration=1,
        max_iterations=3,
        failing_file="calculator.py",
        model_name="gemini-3.6-flash",
        truncated_failure_logs=long_traceback,
    )

    assert "## 🤖 AutoFix Autonomous Healing Report" in report
    assert "<summary><b>View Initial Failure Logs & Traceback</b></summary>" in report
    assert "Traceback line" in report
    # Ensure it truncated the lines to reasonable size
    assert len(report.splitlines()) < 80


def test_heal_response_metadata_and_dict_access():
    """Verify HealResponse preserves all 7 required pipeline metadata fields with object & dict access."""
    resp = HealResponse(
        success=True,
        iterations_used=2,
        iterations=2,
        final_code="def add(a, b): return a + b",
        patched_code="def add(a, b): return a + b",
        generated_tests="def test_add(): assert add(1, 2) == 3",
        language="python",
        failing_file="calculator.py",
        model_name="gemini-3.6-flash",
        model_used="gemini-3.6-flash",
        ast_dependencies=["calculator.py", "utils.py"],
        test_summary={"tests/test_calc.py::test_add": {"initial": "Failed ❌", "final": "Passed ✅"}},
    )

    # 1. Attribute access
    assert resp.success is True
    assert resp.iterations == 2
    assert resp.failing_file == "calculator.py"
    assert resp.patched_code == "def add(a, b): return a + b"
    assert resp.ast_dependencies == ["calculator.py", "utils.py"]
    assert resp.test_summary == {"tests/test_calc.py::test_add": {"initial": "Failed ❌", "final": "Passed ✅"}}
    assert resp.model_used == "gemini-3.6-flash"

    # 2. Dict-style subscripting
    assert resp["success"] is True
    assert resp["iterations"] == 2
    assert resp["failing_file"] == "calculator.py"
    assert resp["patched_code"] == "def add(a, b): return a + b"
    assert resp["ast_dependencies"] == ["calculator.py", "utils.py"]
    assert resp["test_summary"] == {"tests/test_calc.py::test_add": {"initial": "Failed ❌", "final": "Passed ✅"}}
    assert resp["model_used"] == "gemini-3.6-flash"

    # 3. Dict methods
    assert resp.get("success") is True
    assert resp.get("non_existent", "default_val") == "default_val"
    assert "success" in resp
    assert "failing_file" in resp

    with pytest.raises(KeyError):
        _ = resp["unknown_key"]


def test_extract_test_summary_parsing():
    """Verify extract_test_summary parses individual test cases from pytest logs."""
    from app.services.orchestrator import extract_test_summary

    initial_fail_logs = """
tests/test_math.py::test_add FAILED [ 50%]
tests/test_math.py::test_subtract PASSED [100%]
============================== FAILURES ==============================
"""
    final_pass_logs = """
tests/test_math.py::test_add PASSED [ 50%]
tests/test_math.py::test_subtract PASSED [100%]
============================== 2 passed ==============================
"""

    summary = extract_test_summary(
        failing_logs=initial_fail_logs,
        final_output=final_pass_logs,
        test_target="tests/test_math.py",
        success=True,
    )

    assert "tests/test_math.py::test_add" in summary
    assert summary["tests/test_math.py::test_add"]["initial"] == "Failed ❌"
    assert summary["tests/test_math.py::test_add"]["final"] == "Passed ✅"
    assert "tests/test_math.py::test_subtract" in summary
    assert summary["tests/test_math.py::test_subtract"]["initial"] == "Passed ✅"
    assert summary["tests/test_math.py::test_subtract"]["final"] == "Passed ✅"


def test_build_healing_audit_report_with_test_summary_mapping():
    """Verify build_healing_audit_report generates table rows from test_summary mapping."""
    test_summary = {
        "tests/test_foo.py::test_alpha": {"initial": "Failed ❌", "final": "Passed ✅"},
        "tests/test_foo.py::test_beta": {"initial": "Failed ❌", "final": "Passed ✅"},
    }

    report = build_healing_audit_report(
        status="Verified Passing ✅",
        iteration=1,
        max_iterations=3,
        failing_file="foo.py",
        model_name="gemini-3.6-flash",
        test_summary=test_summary,
        patched_code="x = 10",
        language="python",
    )

    assert "| `tests/test_foo.py::test_alpha` | Failed ❌ | Passed ✅ |" in report
    assert "| `tests/test_foo.py::test_beta` | Failed ❌ | Passed ✅ |" in report


@pytest.mark.anyio
async def test_post_pr_comment_with_metadata_and_truncation():
    """Verify post_pr_comment accepts metadata dict, truncates failure logs >2000 chars, and posts to GitHub API."""
    from unittest.mock import AsyncMock, MagicMock, patch

    service = GitHubService()
    huge_failure_logs = ("X" * 3500) + "\nAssertionError: calculation failed"
    metadata = {
        "success": True,
        "iterations": 1,
        "max_iterations": 3,
        "failing_file": "app/calculator.py",
        "model_used": "gemini-3.6-flash",
        "ast_dependencies": ["app/calculator.py", "tests/test_calculator.py"],
        "test_target": "tests/test_calculator.py",
        "patched_code": "def calculate(): return 42",
        "language": "python",
        "failure_logs": huge_failure_logs,
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 201

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp) as mock_post:
        success = await service.post_pr_comment(
            repo_full_name="org/repo",
            pr_number=99,
            comment_or_metadata=metadata,
            token="ghs_secret_token",
        )

        assert success is True
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        call_headers = mock_post.call_args[1]["headers"]
        call_payload = mock_post.call_args[1]["json"]

        assert call_url == "https://api.github.com/repos/org/repo/issues/99/comments"
        assert call_headers["Authorization"] == "token ghs_secret_token"
        posted_body = call_payload["body"]
        assert "## 🤖 AutoFix Autonomous Healing Report" in posted_body
        assert "| **Target Module** | `app/calculator.py` |" in posted_body
        assert "AssertionError: calculation failed" in posted_body
        # Verify failure logs section was truncated to <= 2000 characters
        assert len(huge_failure_logs) > 3500
        # The raw 3500-char string should NOT be intact in the payload
        assert ("X" * 3500) not in posted_body
        assert ("X" * 1500) in posted_body


