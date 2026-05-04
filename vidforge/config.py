"""Runtime settings driven by env vars / .env."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="VIDFORGE_",
        extra="ignore",
    )

    llm_provider: Literal["anthropic", "openai", "deepseek"] = "anthropic"
    llm_model: str = "claude-sonnet-4-5"
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    deepseek_api_key: str | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = Field(
        default="https://api.deepseek.com/v1",
        alias="DEEPSEEK_BASE_URL",
    )
    libtv_access_key: str | None = Field(default=None, alias="LIBTV_ACCESS_KEY")

    width: int = 1080
    height: int = 1920
    fps: int = 30
    default_duration: int = 60

    renderer: Literal["hyperframes", "playwright"] = "playwright"
    tts_voice: str = "zh-CN-XiaoxiaoNeural"

    build_dir: Path = PROJECT_ROOT / "build"
    data_dir: Path = PROJECT_ROOT / "data"
    templates_dir: Path = PROJECT_ROOT / "templates"
    inbox_dir: Path = PROJECT_ROOT / "inbox"
    runs_dir: Path = PROJECT_ROOT / "runs"

    watch_interval_s: float = 2.0

    def ensure_dirs(self) -> None:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
