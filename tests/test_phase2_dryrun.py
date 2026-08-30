import asyncio
import logging
from pathlib import Path
import sys

# Ensure repository root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.agents.healer import generate_code_patch
from app.agents.test_engineer import generate_test_suite
from app.core.config import settings
from app.services.sandboxes import get_sandbox

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("phase2_dryrun")


async def main():
    print("=" * 70)
    print("🚀 AutoFix Phase 2 Multi-Agent Dry Run")
    print("=" * 70)

    sandbox = get_sandbox("python")

    # 1. Intentional Bug Scenario
    buggy_code = """def calculate_average(numbers: list[float]) -> float:
    # Bug: crashes on empty list with ZeroDivisionError, ignores first element
    total = sum(numbers[1:])
    return total / len(numbers)
"""

    requirements = """1. Function signature: `calculate_average(numbers: list[float]) -> float`
2. Compute and return the arithmetic mean of a list of numbers.
3. Return `0.0` if the input list `numbers` is empty.
4. Support lists containing positive floats, negative floats, and zeros.
"""

    print("\n[Input] Buggy Target Code:\n" + buggy_code)
    print("[Input] Requirements:\n" + requirements)

    # Step 1: Generate Test Suite via Test Engineer Agent
    print("\n--- Step 1: Generating Pytest Suite via QA Test Engineer ---")
    test_code = await generate_test_suite(
        target_code=buggy_code,
        requirements=requirements,
    )
    print(f"Generated Test Suite ({len(test_code)} chars):\n{test_code}\n")
    assert "def test_" in test_code, "Expected generated test suite to have test functions"

    # Step 2: Write target and test files into isolated sandbox
    print("--- Step 2: Writing files to Sandbox ---")
    sandbox.clean(settings.WORKSPACE_DIR)
    sandbox.write_files(
        settings.WORKSPACE_DIR,
        {
            "target.py": buggy_code,
            "test_target.py": test_code,
        },
    )
    print(f"Wrote files to sandbox: {settings.WORKSPACE_DIR}")

    # Step 3: Run Initial Pytest Execution (Expected to Fail)
    print("\n--- Step 3: Initial Sandbox Run (Expecting Failures) ---")
    initial_passed, initial_output = sandbox.run_tests(settings.WORKSPACE_DIR, timeout=15)
    print(f"Initial Test Result -> Passed: {initial_passed}")
    print(f"Initial Output/Traceback:\n{initial_output}\n")
    assert not initial_passed, "Expected initial buggy code to fail generated tests"

    # Step 4: Healer Agent generates code patch based on failure tracebacks
    print("--- Step 4: Healer Agent Diagnosing & Patching Code ---")
    healed_code = await generate_code_patch(
        buggy_code=buggy_code,
        requirements=requirements,
        error_logs=initial_output,
    )
    print(f"Patched Code Generated:\n{healed_code}\n")
    assert "def calculate_average" in healed_code, "Expected healed code to define calculate_average"

    # Step 5: Overwrite sandbox target.py and re-run tests
    print("--- Step 5: Verification Sandbox Run with Patched Code ---")
    sandbox.write_files(settings.WORKSPACE_DIR, {"target.py": healed_code})
    final_passed, final_output = sandbox.run_tests(settings.WORKSPACE_DIR, timeout=15)
    print(f"Final Test Result -> Passed: {final_passed}")
    print(f"Final Output:\n{final_output}\n")

    assert final_passed, f"Expected final patched code to pass all tests, but failed with:\n{final_output}"

    print("=" * 70)
    print("🎉 Phase 2 Dry Run Succeeded: Buggy code was autonomously tested, diagnosed, and healed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
