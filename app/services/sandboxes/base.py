from abc import ABC, abstractmethod
import os
from pathlib import Path
import subprocess
import sys


class BaseSandbox(ABC):
    """Abstract base class defining the interface for language-specific execution sandboxes."""

    @abstractmethod
    def write_files(self, workspace: Path, files: dict[str, str]) -> None:
        """Write files to workspace directory safely."""
        pass

    @abstractmethod
    def run_tests(self, workspace: Path, timeout: int = 30) -> tuple[bool, str]:
        """Run tests within the workspace and return (passed, output)."""
        pass

    @abstractmethod
    def clean(self, workspace: Path) -> None:
        """Remove build artifacts, caches, and temp files from the workspace."""
        pass

    def _execute_command(
        self,
        cmd: list[str],
        cwd: Path,
        timeout: int = 30,
        env: dict = None,
    ) -> tuple[bool, str]:
        """Execute a subprocess command with capture_output and timeout handling."""
        try:
            result = subprocess.run(
                cmd,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            output = f"{result.stdout}\n{result.stderr}"
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, f"Execution timed out after {timeout} seconds."
        except Exception as e:
            return False, f"Sandbox execution error: {str(e)}"
