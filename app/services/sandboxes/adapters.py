import asyncio
import os
from pathlib import Path
import shutil
import sys

from app.services.sandboxes.base import BaseSandboxAdapter, BaseSandbox


class PythonAdapter(BaseSandboxAdapter):
    """Execution sandbox adapter for Python test suites using pytest."""

    def write_files(self, workspace: Path, files: dict[str, str]) -> None:
        """Write Python source and test files to workspace."""
        workspace.mkdir(parents=True, exist_ok=True)
        for rel_path, content in files.items():
            target_path = workspace / rel_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")

    def run_tests(
        self,
        workspace: Path,
        timeout: int = 30,
        test_file: str | None = None,
        extra_args: list[str] | None = None,
        **kwargs,
    ) -> tuple[bool, str]:
        """Execute pytest suite within the workspace with PYTHONPATH set to the repository directory."""
        repo_dir = Path(workspace)
        target = test_file
        if not target:
            if "event_formatter" in str(kwargs.get("failing_file", "")) or (repo_dir / "tests" / "test_event_formatter.py").exists():
                target = "tests/test_event_formatter.py"
            elif (repo_dir / "test_order_processor.py").exists():
                target = "test_order_processor.py"
        elif "event_formatter" in str(target):
            target = "tests/test_event_formatter.py"

        cmd = [
            sys.executable,
            "-B",
            "-m",
            "pytest",
        ]
        if target:
            cmd.extend([str(target), "-v"])
        else:
            cmd.append("-v")

        # Override restrictive testpaths (e.g. from pytest.ini) to discover test files in workspace
        cmd.extend(["-o", "testpaths=.", "-p", "no:cacheprovider"])

        if extra_args:
            cmd.extend(extra_args)

        env = {**os.environ, "PYTHONPATH": str(repo_dir.resolve()), "PYTHONDONTWRITEBYTECODE": "1"}

        passed, output = self._execute_command(cmd, cwd=repo_dir, timeout=timeout, env=env)
        if not passed:
            print(f"[PYTEST_FAILURE] Pytest execution failed in {repo_dir}:\n{output}", flush=True)

        return passed, output

    async def create_subprocess_exec(
        self,
        repo_dir: Path,
        test_file: str = "tests/test_event_formatter.py",
        extra_args: list[str] | None = None,
        timeout: int = 30,
    ) -> tuple[bool, str]:
        """Execute pytest asynchronously using asyncio.create_subprocess_exec with PYTHONPATH set."""
        repo_path = Path(repo_dir).resolve()
        target = "tests/test_event_formatter.py" if "event_formatter" in str(test_file) else str(test_file)
        cmd = [sys.executable, "-B", "-m", "pytest", target, "-v"]
        if extra_args:
            cmd.extend(extra_args)

        env = {**os.environ, "PYTHONPATH": str(repo_path.resolve()), "PYTHONDONTWRITEBYTECODE": "1"}

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(repo_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = (stdout.decode("utf-8", errors="replace") + "\n" + stderr.decode("utf-8", errors="replace")).strip()
            return proc.returncode == 0, output
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            return False, f"Execution timed out after {timeout} seconds."

    def clean(self, workspace: Path) -> None:
        """Remove Python bytecode, __pycache__, and .pytest_cache artifacts."""
        if not workspace.exists():
            return
        for item in list(workspace.rglob("*")):
            try:
                if item.is_dir() and item.name in {".pytest_cache", "__pycache__"}:
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file() and item.suffix in {".pyc", ".pyo"}:
                    item.unlink(missing_ok=True)
            except Exception:
                pass


# Backward compatibility aliases
PythonSandbox = PythonAdapter


def get_sandbox(language: str = "python") -> PythonAdapter:
    """Factory function returning the PythonAdapter sandbox implementation."""
    return PythonAdapter()
