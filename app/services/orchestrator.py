from datetime import datetime, timezone
import logging
from typing import List

from app.agents.healer import generate_code_patch
from app.agents.test_engineer import generate_test_suite
from app.core.config import settings
from app.models.schemas import FilePatch, HealRequest, HealResponse, LogEntry
from app.services.sandboxes import get_sandbox

logger = logging.getLogger(__name__)


async def run_healing_pipeline(request: HealRequest) -> HealResponse:
    """Orchestrate the multi-agent code self-healing pipeline."""
    logs: List[LogEntry] = []
    sandbox = get_sandbox(request.language)

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
        message=f"AutoFix multi-agent healing pipeline initialized for language: {request.language}.",
    )

    # Step 1 (Setup): Generate test suite via Test Engineer Agent
    try:
        add_log(
            step_name="TEST_GENERATION",
            message="Test Engineer Agent: Synthesizing comprehensive test suite based on code and requirements...",
        )
        generated_tests = await generate_test_suite(
            target_code=request.buggy_code,
            requirements=request.requirements,
        )
        add_log(
            step_name="TEST_GENERATION_SUCCESS",
            message=f"Test suite successfully synthesized:\n```\n{generated_tests}\n```",
        )
    except Exception as e:
        error_msg = f"Test suite generation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations_used=0,
            final_code=request.buggy_code,
            generated_tests="",
            language=request.language,
            patches=[],
            logs=logs,
        )

    # Step 2 (Sandbox Prep): Clean workspace and write files
    test_filename = "test_target.py" if request.language.lower() == "python" else "Tests"
    try:
        sandbox.clean(settings.WORKSPACE_DIR)
        sandbox.write_files(
            settings.WORKSPACE_DIR,
            {
                request.file_path: request.buggy_code,
                test_filename: generated_tests,
            },
        )
        add_log(
            step_name="SANDBOX_PREP",
            message=f"Cleaned sandbox environment and wrote initial {request.file_path} and {test_filename}.",
        )
    except Exception as e:
        error_msg = f"Sandbox preparation failed: {str(e)}"
        add_log(step_name="ERROR", message=error_msg)
        return HealResponse(
            success=False,
            iterations_used=0,
            final_code=request.buggy_code,
            generated_tests=generated_tests,
            language=request.language,
            patches=[],
            logs=logs,
        )

    # Step 3 (The Loop): Test, Diagnose, Patch, and Re-verify
    iteration = 0
    success = False
    current_code = request.buggy_code

    while iteration < settings.MAX_HEALING_ITERATIONS:
        add_log(
            step_name="SANDBOX_RUN",
            message=f"Running test suite in sandbox (Iteration #{iteration})...",
        )

        passed, output = sandbox.run_tests(settings.WORKSPACE_DIR, timeout=15)

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
            patched_code = await generate_code_patch(
                buggy_code=current_code,
                requirements=request.requirements,
                error_logs=output,
            )
            current_code = patched_code
            sandbox.write_files(
                settings.WORKSPACE_DIR,
                {request.file_path: patched_code},
            )
            add_log(
                step_name="CODE_PATCHED",
                message=f"Healer Agent (Iteration {iteration}/{settings.MAX_HEALING_ITERATIONS}): Patched {request.file_path} written to sandbox:\n```\n{patched_code}\n```",
            )
        except Exception as e:
            error_msg = f"Healer Agent failed on iteration {iteration}: {str(e)}"
            add_log(step_name="ERROR", message=error_msg)
            break

    # Step 4 (Wrap Up): Read final version of target code from sandbox
    target_file = settings.WORKSPACE_DIR / request.file_path
    if target_file.exists():
        final_code = target_file.read_text(encoding="utf-8")
    else:
        final_code = current_code

    add_log(
        step_name="PIPELINE_COMPLETE",
        message=f"Pipeline finished. Success: {success}, Iterations: {iteration}/{settings.MAX_HEALING_ITERATIONS}.",
    )

    patches = []
    if final_code != request.buggy_code:
        patches.append(
            FilePatch(
                file_path=request.file_path,
                original_content=request.buggy_code,
                patched_content=final_code,
            )
        )

    return HealResponse(
        success=success,
        iterations_used=iteration,
        final_code=final_code,
        generated_tests=generated_tests,
        language=request.language,
        patches=patches,
        logs=logs,
    )
