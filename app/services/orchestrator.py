from datetime import datetime, timezone
import logging
from typing import List

from app.agents.healer import generate_code_patch
from app.agents.test_engineer import generate_test_suite
from app.core.config import settings
from app.models.schemas import HealRequest, HealResponse, LogEntry
from app.services.sandbox import clean_sandbox, run_pytest, write_file

logger = logging.getLogger(__name__)


async def run_healing_pipeline(request: HealRequest) -> HealResponse:
    """Orchestrate the multi-agent code self-healing pipeline."""
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
        step_name="PIPELINE_INIT",
        message="AutoFix multi-agent healing pipeline initialized.",
    )

    # Step 1 (Setup): Generate test suite via Test Engineer Agent
    try:
        add_log(
            step_name="TEST_GENERATION",
            message="Test Engineer Agent: Synthesizing comprehensive pytest suite based on code and requirements...",
        )
        generated_tests = await generate_test_suite(
            target_code=request.buggy_code,
            requirements=request.requirements,
        )
        add_log(
            step_name="TEST_GENERATION_SUCCESS",
            message=f"Test suite successfully synthesized:\n```python\n{generated_tests}\n```",
        )
    except Exception as e:
        error_msg = f"Test suite generation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations_used=0,
            final_code=request.buggy_code,
            generated_tests="",
            logs=logs,
        )

    # Step 2 (Sandbox Prep): Clean workspace and write files
    try:
        clean_sandbox()
        write_file("target.py", request.buggy_code)
        write_file("test_target.py", generated_tests)
        add_log(
            step_name="SANDBOX_PREP",
            message="Cleaned sandbox environment and wrote initial target.py and test_target.py.",
        )
    except Exception as e:
        error_msg = f"Sandbox preparation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations_used=0,
            final_code=request.buggy_code,
            generated_tests=generated_tests,
            logs=logs,
        )

    # Step 3 (The Loop): Test, Diagnose, Patch, and Re-verify
    iteration = 0
    success = False
    current_code = request.buggy_code

    while iteration < settings.MAX_HEALING_ITERATIONS:
        add_log(
            step_name="SANDBOX_RUN",
            message=f"Running pytest suite in sandbox (Iteration #{iteration})...",
        )

        passed, output = run_pytest(timeout_seconds=15)

        if passed:
            add_log(
                step_name="TESTS_PASSED",
                message=f"All test assertions passed successfully on iteration {iteration}!\n{output}",
            )
            success = True
            break

        # Tests failed
        iteration += 1
        add_log(
            step_name="TESTS_FAILED",
            message=f"Pytest execution failed on iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}:\n{output}",
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
            patched_code = await generate_code_patch(
                buggy_code=current_code,
                requirements=request.requirements,
                error_logs=output,
            )
            current_code = patched_code
            write_file("target.py", patched_code)
            add_log(
                step_name="CODE_PATCHED",
                message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Patched target.py written to sandbox:\n```python\n{patched_code}\n```",
            )
        except Exception as e:
            error_msg = f"Healer Agent failed on iteration {iteration}: {str(e)}"
            add_log(step_name="ERROR", message=error_msg)
            break

    # Step 4 (Wrap Up): Read final version of target code from sandbox
    target_file = settings.WORKSPACE_DIR / "target.py"
    if target_file.exists():
        final_code = target_file.read_text(encoding="utf-8")
    else:
        final_code = current_code

    add_log(
        step_name="PIPELINE_COMPLETE",
        message=f"Pipeline finished. Success: {success}, Iterations: {iteration}/{settings.MAX_HEALING_ITERATIONS}.",
    )

    return HealResponse(
        success=success,
        iterations_used=iteration,
        final_code=final_code,
        generated_tests=generated_tests,
        logs=logs,
    )
