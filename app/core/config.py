from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Base directory of the repository
BASE_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    """Application configuration settings loaded from environment variables or .env file."""

    GOOGLE_API_KEY: str = ""
    PRIMARY_MODEL: str = "gemini-2.5-pro"
    FAST_MODEL: str = "gemini-2.5-flash"
    WORKSPACE_DIR: Path = Path("./workspace")
    MAX_HEALING_ITERATIONS: int = 3

    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("WORKSPACE_DIR", mode="after")
    @classmethod
    def resolve_workspace_dir(cls, v: Path | str) -> Path:
        """Resolve WORKSPACE_DIR to an absolute Path and ensure the directory exists."""
        path = Path(v)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path


# Global settings instance
settings = Settings()
