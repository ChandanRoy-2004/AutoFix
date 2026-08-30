from datetime import datetime, timezone
from pydantic import BaseModel, Field


class HealRequest(BaseModel):
    """Request schema for initiating the self-healing code pipeline."""

    language: str = Field(
        default="python",
        description="Target programming language: python, csharp, java",
    )
    buggy_code: str = Field(..., description="Target source code")
    requirements: str = Field(..., description="Functional requirements")
    file_path: str = Field(
        default="target.py",
        description="Relative file path within repository",
    )


class RepoAnalysisRequest(BaseModel):
    """Request schema for repository-level analysis and diagnosis."""

    repo_url: str = Field(..., description="Repository URL to analyze")
    branch: str = Field(default="main", description="Target branch name")
    failing_test_output: str = Field(..., description="Failing test output logs")


class FilePatch(BaseModel):
    """Schema representing a file patch with original and updated contents."""

    file_path: str = Field(..., description="Relative file path within repository")
    original_content: str = Field(..., description="Original content before patch")
    patched_content: str = Field(..., description="Patched content after repair")


class LogEntry(BaseModel):
    """Schema representing an individual trace or log event in the healing pipeline."""

    step_name: str = Field(
        ...,
        description="Name of the pipeline step (e.g. TEST_GENERATION, EXECUTION_FAILURE, CODE_PATCH)",
    )
    message: str = Field(..., description="Detailed log message for this step")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO format timestamp of the log event",
    )


class HealResponse(BaseModel):
    """Response schema containing the outcome of the self-healing pipeline."""

    success: bool = Field(
        ...,
        description="Whether the self-healing pipeline resolved all errors and passed all tests",
    )
    iterations_used: int = Field(
        ...,
        description="Total number of healing iterations executed",
    )
    final_code: str = Field(
        ...,
        description="The final healed or latest version of the Python script",
    )
    generated_tests: str = Field(
        ...,
        description="The generated pytest test suite used for verification",
    )
    language: str = Field(
        default="python",
        description="Target programming language",
    )
    patches: list[FilePatch] = Field(
        default_factory=list,
        description="List of file patches applied during healing",
    )
    logs: list[LogEntry] = Field(
        default_factory=list,
        description="Ordered list of log entries and trace events from the healing loop",
    )
