"""Runtime settings driven by env vars / .env."""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="VIDFORGE_",
        extra="ignore",
    )

    llm_provider: Literal["anthropic", "openai", "deepseek"] = "deepseek"
    llm_model: str = "deepseek-v4-pro"
    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "VIDFORGE_DEEPSEEK_API_KEY"),
    )
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

    @model_validator(mode="after")
    def _normalize_api_keys(self) -> Self:
        for name in ("anthropic_api_key", "openai_api_key", "deepseek_api_key"):
            v = getattr(self, name)
            if v == "":
                setattr(self, name, None)
        return self

    @model_validator(mode="after")
    def _use_deepseek_when_legacy_provider_has_no_key(self) -> Self:
        """`.env` often keeps `VIDFORGE_LLM_PROVIDER=openai` from an old setup while
        only `DEEPSEEK_API_KEY` is present — route to DeepSeek instead of failing
        or hitting the wrong vendor.
        """
        if not self.deepseek_api_key:
            return self
        if self.llm_provider == "openai" and not self.openai_api_key:
            self.llm_provider = "deepseek"
            lm = (self.llm_model or "").lower()
            if "gpt" in lm or lm.startswith("o1") or lm.startswith("o3"):
                self.llm_model = "deepseek-v4-pro"
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            self.llm_provider = "deepseek"
            if "claude" in (self.llm_model or "").lower():
                self.llm_model = "deepseek-v4-pro"
        return self

    def ensure_dirs(self) -> None:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
