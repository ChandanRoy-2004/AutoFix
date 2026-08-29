import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Tuple

from app.core.config import settings

logger = logging.getLogger(__name__)


class SandboxService:
    """Isolated execution service for writing files and running pytest suites in WORKSPACE_DIR."""

    def __init__(self, workspace_dir: Path | None = None):
        self.workspace_dir = workspace_dir or settings.WORKSPACE_DIR
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def write_file(self, filename: str, content: str) -> Path:
        """Write string content into settings.WORKSPACE_DIR / filename, ensuring parent directories exist."""
        file_path = self.workspace_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        logger.debug("Wrote %d bytes to %s", len(content), file_path)
        return file_path

    def clean_sandbox(self) -> None:
        """Clean up temporary test artifacts, .pytest_cache, and __pycache__ in the workspace."""
        for item in self.workspace_dir.iterdir():
            try:
                if item.is_dir() and item.name in {".pytest_cache", "__pycache__"}:
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file() and (item.suffix in {".pyc", ".pyo"} or item.name in {"target.py", "test_target.py"}):
                    item.unlink(missing_ok=True)
            except Exception as e:
                logger.warning("Failed to clean sandbox item %s: %s", item, e)

    def run_pytest(self, timeout_seconds: int = 15) -> Tuple[bool, str]:
        """Execute pytest within settings.WORKSPACE_DIR using subprocess.run."""
        cmd = [
            sys.executable,
            "-B",  # Don't write .pyc bytecode cache
            "-m",
            "pytest",
            "-v",
            "-p",
            "no:cacheprovider",
        ]

        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Add workspace directory to PYTHONPATH so target imports resolve correctly
        workspace_abs = str(self.workspace_dir.resolve())
        env["PYTHONPATH"] = f"{workspace_abs}:{env.get('PYTHONPATH', '')}"

        try:
            result = subprocess.run(
                cmd,
                cwd=str(self.workspace_dir),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )

            if result.returncode == 0:
                logger.info("Pytest execution succeeded in %s", self.workspace_dir)
                return True, result.stdout
            else:
                combined_output = f"{result.stdout}\n{result.stderr}".strip()
                logger.info("Pytest execution failed with exit code %d in %s", result.returncode, self.workspace_dir)
                return False, combined_output

        except subprocess.TimeoutExpired:
            error_msg = f"Execution timed out after {timeout_seconds} seconds."
            logger.warning(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Sandbox execution error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return False, error_msg


# Default instance
sandbox_service = SandboxService()

# Functional utility wrappers
write_file = sandbox_service.write_file
clean_sandbox = sandbox_service.clean_sandbox
run_pytest = sandbox_service.run_pytest
