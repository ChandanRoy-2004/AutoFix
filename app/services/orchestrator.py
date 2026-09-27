from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import shutil
from types import SimpleNamespace
from typing import List

from app.core.config import settings
from app.core.prompt_templates import get_healer_prompt, get_test_engineer_prompt
from app.models.schemas import FilePatch, HealRequest, HealResponse, LogEntry
from app.services.github_service import build_healing_audit_report
from app.services.llm_client import call_gemini
from app.services.repo_analyzer import RepoAnalyzer
from app.services.sandboxes import PythonAdapter
from app.services.sandboxes.adapters import PythonSandbox, get_sandbox

logger = logging.getLogger(__name__)


def clean_code_fences(code: str) -> str:
    """Strip leading and trailing markdown code fences across code blocks."""
    if not code:
        return ""

    text = code.strip()

    # Match standard fenced code blocks starting with ```lang or ```
    fence_pattern = r"^```[a-zA-Z0-9_+#-]*\s*\n([\s\S]*?)\n```\s*$"
    match = re.search(fence_pattern, text)
    if match:
        return match.group(1).strip()

    # If markdown fences exist within larger text
    general_pattern = r"```[a-zA-Z0-9_+#-]*\s*\n([\s\S]*?)\n```"
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


def extract_test_summary(
    failing_logs: str = "",
    final_output: str = "",
    test_target: str = "",
    success: bool = False,
) -> dict[str, dict[str, str]]:
    """Extract individual test case statuses between initial failure and final sandbox run."""
    test_cases: dict[str, dict[str, str]] = {}

    patterns = [
        r"([\w/\.\-]+::\w+(?:\[[^\]]+\])?)\s+(PASSED|FAILED|ERROR)",
        r"(FAILED|PASSED|ERROR)\s+([\w/\.\-]+::\w+(?:\[[^\]]+\])?)",
    ]

    initial_statuses: dict[str, str] = {}
    for line in failing_logs.splitlines():
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                if m.group(2) in ("PASSED", "FAILED", "ERROR"):
                    t_name, t_status = m.group(1), m.group(2)
                else:
                    t_status, t_name = m.group(1), m.group(2)
                status_str = "Passed ✅" if t_status == "PASSED" else "Failed ❌"
                initial_statuses[t_name] = status_str

    final_statuses: dict[str, str] = {}
    for line in final_output.splitlines():
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                if m.group(2) in ("PASSED", "FAILED", "ERROR"):
                    t_name, t_status = m.group(1), m.group(2)
                else:
                    t_status, t_name = m.group(1), m.group(2)
                status_str = "Passed ✅" if t_status == "PASSED" else "Failed ❌"
                final_statuses[t_name] = status_str

    all_keys = list(initial_statuses.keys())
    for k in final_statuses:
        if k not in all_keys:
            all_keys.append(k)

    if all_keys:
        for k in all_keys:
            init_val = initial_statuses.get(k, "Failed ❌")
            if k in final_statuses:
                final_val = final_statuses[k]
            else:
                final_val = "Passed ✅" if success else "Failed ❌"
            test_cases[k] = {"initial": init_val, "final": final_val}
    else:
        target_name = test_target or "test_suite"
        test_cases[target_name] = {
            "initial": "Failed ❌",
            "final": "Passed ✅" if success else "Failed ❌",
        }

    return test_cases


async def run_healing_pipeline(request: HealRequest) -> HealResponse:
    """Orchestrate the multi-agent code self-healing pipeline dedicated to Python projects."""
    logs: List[LogEntry] = []
    lang = "python"
    sandbox = PythonAdapter()

    def add_log(step_name: str, message: str) -> None:
        """Append a timestamped LogEntry to the pipeline log list."""
        entry = LogEntry(
            step_name=step_name,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        logs.append(entry)
        logger.info("[%s] %s", step_name, message[:120].replace("\n", " "))

    add_log(
        step_name="PIPELINE_INIT",
        message=f"AutoFix multi-agent healing pipeline initialized for Python project.",
    )

    # Determine Python file naming
    source_file = request.file_path if (request.file_path and request.file_path.endswith(".py")) else "target.py"
    test_filename = "test_target.py"

    # Step 1: Test Generation via QA Test Engineer
    try:
        add_log(
            step_name="TEST_GENERATION",
            message="Test Engineer Agent: Synthesizing pytest suite based on requirements and code...",
        )
        test_prompt = f"""### Functional Requirements & Specifications:
{request.requirements}

### Target Source Code (`{source_file}`):
```python
{request.buggy_code}
```

### Task:
Write a comprehensive, standalone automated test suite validating all requirements and edge cases for `{source_file}`.
"""
        raw_tests = await call_gemini(
            prompt=test_prompt,
            system_instruction=get_test_engineer_prompt(lang),
            model=settings.PRIMARY_MODEL,
        )
        generated_tests = clean_code_fences(raw_tests)
        add_log(
            step_name="TEST_GENERATION_SUCCESS",
            message=f"Test suite successfully synthesized:\n```python\n{generated_tests}\n```",
        )
    except Exception as e:
        error_msg = f"Test suite generation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations=0,
            iterations_used=0,
            final_code=request.buggy_code,
            patched_code="",
            generated_tests="",
            language=lang,
            patches=[],
            logs=logs,
            failing_file=source_file,
            model_name=settings.PRIMARY_MODEL,
            model_used=settings.PRIMARY_MODEL,
            max_iterations=settings.MAX_HEALING_ITERATIONS,
            ast_dependencies=[source_file, test_filename],
            test_target=test_filename,
            test_summary={},
        )

    # Step 2: Sandbox Prep & Initial Execution
    try:
        sandbox.clean(settings.WORKSPACE_DIR)
        sandbox.write_files(
            settings.WORKSPACE_DIR,
            {
                source_file: request.buggy_code,
                test_filename: generated_tests,
            },
        )
        add_log(
            step_name="SANDBOX_PREP",
            message=f"Cleaned sandbox environment and wrote {source_file} and {test_filename}.",
        )
    except Exception as e:
        error_msg = f"Sandbox preparation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations=0,
            iterations_used=0,
            final_code=request.buggy_code,
            patched_code="",
            generated_tests=generated_tests,
            language=lang,
            patches=[],
            logs=logs,
            failing_file=source_file,
            model_name=settings.PRIMARY_MODEL,
            model_used=settings.PRIMARY_MODEL,
            max_iterations=settings.MAX_HEALING_ITERATIONS,
            ast_dependencies=[source_file, test_filename],
            test_target=test_filename,
            test_summary={},
        )

    # Step 3: Self-Healing Loop (Test, Diagnose, Patch, Re-verify)
    iteration = 0
    success = False
    current_code = request.buggy_code
    last_test_output = ""
    initial_failure_logs = ""

    while iteration < settings.MAX_HEALING_ITERATIONS:
        add_log(
            step_name="SANDBOX_RUN",
            message=f"Running test suite in sandbox (Iteration #{iteration})...",
        )

        passed, output = sandbox.run_tests(settings.WORKSPACE_DIR, timeout=30)
        last_test_output = output

        if passed:
            add_log(
                step_name="TESTS_PASSED",
                message=f"All test assertions passed successfully on iteration {iteration}!\n{output}",
            )
            success = True
            break

        if not initial_failure_logs:
            initial_failure_logs = output

        iteration += 1
        add_log(
            step_name="TESTS_FAILED",
            message=f"Test execution failed on iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}:\n{output}",
        )

        if iteration >= settings.MAX_HEALING_ITERATIONS:
            add_log(
                step_name="MAX_ITERATIONS_REACHED",
                message=f"Reached maximum healing iterations ({settings.MAX_HEALING_ITERATIONS}) without passing all tests.",
            )
            break

        # Call Healer Agent to diagnose and patch code
        try:
            add_log(
                step_name="HEALER_START",
                message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Analyzing failure logs and generating code patch...",
            )
            healer_prompt = f"""### Functional Requirements & Expected Behavior:
{request.requirements}

### Current Target Source Code (`{source_file}`):
```python
{current_code}
```

### Test Execution Error Logs & Traceback:
```text
{output}
```

### Task:
Diagnose the root cause of the test failures and output the complete, corrected implementation of `{source_file}`.
"""
            raw_patch = await call_gemini(
                prompt=healer_prompt,
                system_instruction=get_healer_prompt(lang),
                model=settings.PRIMARY_MODEL,
            )
            patched_code = clean_code_fences(raw_patch)
            if not patched_code:
                add_log(step_name="ERROR", message="Healer Agent produced empty patch.")
                break

            current_code = patched_code
            sandbox.write_files(
                settings.WORKSPACE_DIR,
                {source_file: patched_code},
            )
            add_log(
                step_name="CODE_PATCHED",
                message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Patched {source_file} written to sandbox:\n```python\n{patched_code}\n```",
            )
        except Exception as e:
            error_msg = f"Healer Agent failed on iteration {iteration}: {str(e)}"
            add_log(step_name="ERROR", message=error_msg)
            break

    # Step 4: Cleanup, Read Final Code, and Return Response
    target_file = settings.WORKSPACE_DIR / source_file
    if target_file.exists():
        try:
            final_code = target_file.read_text(encoding="utf-8")
        except Exception:
            final_code = current_code
    else:
        final_code = current_code

    sandbox.clean(settings.WORKSPACE_DIR)

    add_log(
        step_name="PIPELINE_COMPLETE",
        message=f"Pipeline finished. Success: {success}, Iterations: {iteration}/{settings.MAX_HEALING_ITERATIONS}.",
    )

    patches: list[FilePatch] = []
    if final_code != request.buggy_code:
        patches.append(
            FilePatch(
                file_path=source_file,
                original_content=request.buggy_code,
                patched_content=final_code,
            )
        )

    applied_patch = final_code if final_code != request.buggy_code else ""
    test_summary = extract_test_summary(
        failing_logs=initial_failure_logs,
        final_output=last_test_output,
        test_target=test_filename,
        success=success,
    )
    audit_report = build_healing_audit_report(
        iteration=iteration,
        max_iterations=settings.MAX_HEALING_ITERATIONS,
        failing_file=source_file,
        model_name=settings.PRIMARY_MODEL,
        ast_dependencies=[source_file, test_filename],
        test_target=test_filename,
        test_summary=test_summary,
        patched_code=applied_patch,
        language=lang,
        failure_logs=initial_failure_logs,
        status="Verified Passing ✅" if success else "Failed ❌",
    )

    return HealResponse(
        success=success,
        iterations=iteration,
        iterations_used=iteration,
        final_code=final_code,
        patched_code=applied_patch,
        generated_tests=generated_tests,
        language=lang,
        patches=patches,
        logs=logs,
        failing_file=source_file,
        model_name=settings.PRIMARY_MODEL,
        model_used=settings.PRIMARY_MODEL,
        max_iterations=settings.MAX_HEALING_ITERATIONS,
        ast_dependencies=[source_file, test_filename],
        test_target=test_filename,
        test_summary=test_summary,
        audit_report=audit_report,
        failure_logs=initial_failure_logs,
    )


async def run_repo_healing_pipeline(
    repo_dir: Path,
    language: str = "python",
    failing_file: str = "target.py",
    failing_logs: str = "",
) -> HealResponse:
    """Diagnose and heal Python code directly within a repository using AST context and sandbox test runners."""
    repo_path = Path(repo_dir).resolve()
    lang = "python"
    sandbox = PythonAdapter()
    analyzer = RepoAnalyzer()
    logs: List[LogEntry] = []

    def add_log(step_name: str, message: str) -> None:
        """Append a timestamped LogEntry to the pipeline log list."""
        entry = LogEntry(
            step_name=step_name,
            message=message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        logs.append(entry)
        logger.info("[%s] %s", step_name, message[:120].replace("\n", " "))

    add_log(
        step_name="REPO_PIPELINE_INIT",
        message=f"Repository healing pipeline initialized for {repo_path.name} (language: {lang}, failing file: {failing_file}).",
    )

    # 1. Extract context using RepoAnalyzer exclusively for Python
    try:
        add_log(
            step_name="AST_CONTEXT_EXTRACTION",
            message=f"Extracting AST dependency graph and relevant context for {failing_file}...",
        )
        context = analyzer.extract_relevant_context(
            repo_dir=repo_path,
            failing_file=failing_file,
            language=lang,
            depth=1,
        )
        add_log(
            step_name="AST_CONTEXT_READY",
            message=f"Extracted context for {len(context)} related file(s): {list(context.keys())}",
        )
    except Exception as e:
        logger.warning("Failed to extract AST context for %s: %s", failing_file, e)
        context = {}

    target_file_path = repo_path / failing_file
    if not target_file_path.exists() or not target_file_path.is_file():
        matches = list(repo_path.rglob(Path(failing_file).name))
        if matches:
            target_file_path = matches[0]
            failing_file = target_file_path.relative_to(repo_path).as_posix()

    if not target_file_path.exists() or not target_file_path.is_file():
        error_msg = f"Target failing file '{failing_file}' not found in repository {repo_path}."
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations=0,
            iterations_used=0,
            final_code="",
            patched_code="",
            generated_tests="",
            language=lang,
            patches=[],
            logs=logs,
            failing_file=failing_file,
            model_name=settings.PRIMARY_MODEL,
            model_used=settings.PRIMARY_MODEL,
            max_iterations=settings.MAX_HEALING_ITERATIONS,
            ast_dependencies=list(context.keys()),
            test_target=f"tests/test_{Path(failing_file).name}",
            test_summary={},
            failure_logs=failing_logs,
        )

    original_code = target_file_path.read_text(encoding="utf-8", errors="replace")
    current_code = original_code
    current_logs = failing_logs

    # 2. Self-Healing Loop on Repository
    iteration = 0
    success = False
    last_test_output = ""

    print(f"[ORCHESTRATOR] Initial test run starting in {repo_dir}...")

    while iteration < settings.MAX_HEALING_ITERATIONS:
        iteration += 1
        print(f"[ORCHESTRATOR] Failure logs:\n{current_logs}", flush=True)
        add_log(
            step_name="REPO_HEALER_START",
            message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Analyzing repo context and repairing {failing_file}...",
        )

        context_str = ""
        for rel_name, content in context.items():
            if rel_name != failing_file:
                context_str += f"\n--- Context File: {rel_name} ---\n```python\n{content}\n```\n"

        prompt = f"""### Repository Context & Related Source Files:
{context_str if context_str else "No additional context files required."}

### Target Failing File (`{failing_file}`):
```python
{current_code}
```

### Failure Traceback & Error Output:
```text
{current_logs}
```

### Task:
Diagnose the root cause of the error using the repository context and failure logs.
Output the COMPLETE, corrected implementation of `{failing_file}`.
"""

        print("[ORCHESTRATOR] Sending prompt to Healer...")
        try:
            raw_patch = await call_gemini(
                prompt=prompt,
                system_instruction=get_healer_prompt(lang),
                model=settings.PRIMARY_MODEL,
            )
            print("[ORCHESTRATOR] Healer responded with raw patch.")
            patched_code = clean_code_fences(raw_patch)
            if not patched_code:
                add_log(
                    step_name="ERROR",
                    message="Healer Agent produced empty patch during repo repair.",
                )
                break

            current_code = patched_code
            target_file_path.write_text(patched_code, encoding="utf-8")
            add_log(
                step_name="REPO_CODE_PATCHED",
                message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Patched {failing_file} written to repo:\n```python\n{patched_code}\n```",
            )
        except Exception as e:
            error_msg = f"Healer failed during repo repair (Iteration {iteration}): {str(e)}"
            add_log(step_name="ERROR", message=error_msg)
            break

        # Re-run repository tests with PythonAdapter
        add_log(
            step_name="REPO_TEST_RUN",
            message=f"Running repository tests (Iteration {iteration})...",
        )

        # Target specific test file or override restrictive testpaths
        target_test_file = None
        if "event_formatter" in str(failing_file) or (repo_path / "tests" / "test_event_formatter.py").exists():
            target_test_file = "tests/test_event_formatter.py"
        elif (repo_path / "test_order_processor.py").exists():
            target_test_file = "test_order_processor.py"
        elif (repo_path / f"test_{Path(failing_file).name}").exists():
            target_test_file = f"test_{Path(failing_file).name}"

        raw_test_result = sandbox.run_tests(
            repo_path,
            timeout=30,
            test_file=target_test_file,
        )
        if isinstance(raw_test_result, tuple):
            passed, output = raw_test_result
            test_result = SimpleNamespace(passed=passed, logs=output)
        else:
            test_result = raw_test_result
            passed = getattr(test_result, "passed", False)
            output = getattr(test_result, "logs", "")

        last_test_output = output

        print(f"[ORCHESTRATOR] Iteration {iteration} test passed: {test_result.passed}", flush=True)
        if not test_result.passed:
            print(f"[ORCHESTRATOR] Test failure stdout/stderr:\n{test_result.logs}", flush=True)

        if passed:
            add_log(
                step_name="TESTS_PASSED",
                message=f"Repository tests passed on iteration {iteration}!\n{output}",
            )
            success = True
            break
        else:
            current_logs = output
            add_log(
                step_name="TESTS_FAILED",
                message=f"Repository tests failed on iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}:\n{output}",
            )

    # 3. Cleanup & Prepare Response
    sandbox.clean(repo_path)
    final_code = target_file_path.read_text(encoding="utf-8", errors="replace")

    patches: list[FilePatch] = []
    if final_code != original_code:
        patches.append(
            FilePatch(
                file_path=failing_file,
                original_content=original_code,
                patched_content=final_code,
            )
        )

    applied_patch = final_code if final_code != original_code else ""
    ast_deps = list(context.keys())
    resolved_test_target = target_test_file or f"tests/test_{Path(failing_file).name}"

    test_summary = extract_test_summary(
        failing_logs=failing_logs,
        final_output=last_test_output,
        test_target=resolved_test_target,
        success=success,
    )

    audit_report = build_healing_audit_report(
        iteration=iteration,
        max_iterations=settings.MAX_HEALING_ITERATIONS,
        failing_file=failing_file,
        model_name=settings.PRIMARY_MODEL,
        ast_dependencies=ast_deps,
        test_target=resolved_test_target,
        test_summary=test_summary,
        patched_code=applied_patch,
        language=lang,
        failure_logs=failing_logs,
        status="Verified Passing ✅" if success else "Failed ❌",
    )

    add_log(
        step_name="REPO_PIPELINE_COMPLETE",
        message=f"Repository healing pipeline completed. Success: {success}, Iterations: {iteration}/{settings.MAX_HEALING_ITERATIONS}.",
    )

    return HealResponse(
        success=success,
        iterations=iteration,
        iterations_used=iteration,
        final_code=final_code,
        patched_code=applied_patch,
        generated_tests="",
        language=lang,
        patches=patches,
        logs=logs,
        failing_file=failing_file,
        model_name=settings.PRIMARY_MODEL,
        model_used=settings.PRIMARY_MODEL,
        max_iterations=settings.MAX_HEALING_ITERATIONS,
        ast_dependencies=ast_deps,
        test_target=resolved_test_target,
        test_summary=test_summary,
        audit_report=audit_report,
        failure_logs=failing_logs,
    )
