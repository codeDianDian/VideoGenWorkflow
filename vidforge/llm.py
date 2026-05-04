"""Thin LLM wrapper that returns a Pydantic model from a system + user prompt.

Two upgrades over the v1:

* **Disk cache** keyed by SHA-256 of (provider, model, system, user, schema).
  Iterating on style or duration without touching prompts -> 0 LLM calls.
* **Hit/miss counters** logged at the end of every run via `report_cache_stats()`.

Set `VIDFORGE_NO_LLM_CACHE=1` to bypass.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from rich.console import Console
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import settings

T = TypeVar("T", bound=BaseModel)
console = Console()


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    bypassed: int = 0

    def reset(self) -> None:
        self.hits = self.misses = self.bypassed = 0


stats = CacheStats()


def _cache_dir() -> "os.PathLike[str]":
    d = settings.build_dir / "llm_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_disabled() -> bool:
    return os.environ.get("VIDFORGE_NO_LLM_CACHE", "").lower() in {"1", "true", "yes"}


def _strip_codefence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
    return text.strip()


def _ask_anthropic(system: str, user: str, max_tokens: int) -> str:
    from anthropic import Anthropic

    if not settings.anthropic_api_key:
        raise RuntimeError("ANTHROPIC_API_KEY missing")
    client = Anthropic(api_key=settings.anthropic_api_key)
    msg = client.messages.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(block.text for block in msg.content if block.type == "text")


def _ask_openai(system: str, user: str, max_tokens: int) -> str:
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI(api_key=settings.openai_api_key)
    rsp = client.chat.completions.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return rsp.choices[0].message.content or ""


def _ask_deepseek(system: str, user: str, max_tokens: int) -> str:
    """DeepSeek speaks the OpenAI chat-completions protocol verbatim, so we
    reuse the OpenAI SDK and only swap the base URL + key.

    Default model: ``deepseek-chat`` (V3.x); set ``VIDFORGE_LLM_MODEL=deepseek-reasoner``
    for R1 if you need stronger reasoning at higher cost/latency. Both honour
    ``response_format={"type": "json_object"}``.
    """
    from openai import OpenAI

    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY missing")
    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
    )
    rsp = client.chat.completions.create(
        model=settings.llm_model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
    )
    return rsp.choices[0].message.content or ""


def _cache_key(system: str, full_user: str, schema: str) -> str:
    h = hashlib.sha256()
    h.update(settings.llm_provider.encode())
    h.update(b"|")
    h.update(settings.llm_model.encode())
    h.update(b"|")
    h.update(system.encode("utf-8"))
    h.update(b"|")
    h.update(full_user.encode("utf-8"))
    h.update(b"|")
    h.update(schema.encode("utf-8"))
    return h.hexdigest()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _ask_llm_raw(system: str, user: str, max_tokens: int) -> str:
    provider = settings.llm_provider
    if provider == "anthropic":
        return _ask_anthropic(system, user, max_tokens)
    if provider == "deepseek":
        return _ask_deepseek(system, user, max_tokens)
    if provider == "openai":
        return _ask_openai(system, user, max_tokens)
    raise ValueError(f"Unsupported llm_provider: {provider!r}")


def ask_json(system: str, user: str, model_cls: type[T], max_tokens: int = 4000) -> T:
    """Return an instance of `model_cls`, served from disk cache when possible."""
    schema = json.dumps(model_cls.model_json_schema(), ensure_ascii=False, sort_keys=True)
    full_user = (
        f"{user}\n\n"
        f"Return ONLY a single JSON object that conforms to this JSON Schema:\n{schema}\n"
        "Do not wrap it in markdown fences. Do not add commentary."
    )

    key = _cache_key(system, full_user, schema)
    cache_path = _cache_dir() / f"{key}.json"

    if _cache_disabled():
        stats.bypassed += 1
    elif cache_path.exists():
        try:
            obj = model_cls.model_validate(json.loads(cache_path.read_text(encoding="utf-8")))
            stats.hits += 1
            console.log(
                f"[dim]\u2299 llm cache hit  {key[:8]}  ({model_cls.__name__})[/dim]"
            )
            return obj
        except Exception as exc:  # corrupt or schema-incompatible
            console.log(f"[yellow]cache corrupt for {key[:8]}: {exc}; refetching[/yellow]")

    stats.misses += 1
    last_err: str | None = None
    obj: T | None = None
    for attempt in range(3):
        suffix = ""
        if last_err:
            suffix = (
                f"\n\nCRITICAL: Your previous reply was NOT valid JSON for this schema — {last_err}. "
                "Return again as ONE compact JSON object only. "
                "Escape every newline inside string values as \\n. Do not truncate mid-string."
            )
        raw = _ask_llm_raw(system, full_user + suffix, max_tokens)
        cleaned = _strip_codefence(raw)
        try:
            data = json.loads(cleaned)
            obj = model_cls.model_validate(data)
            break
        except json.JSONDecodeError as exc:
            last_err = f"JSONDecodeError: {exc}"
            console.log(f"[yellow]ask_json retry {attempt + 1}/3 — {last_err[:160]}[/yellow]")
        except ValidationError as exc:
            last_err = f"ValidationError: {exc}"
            console.log(f"[yellow]ask_json retry {attempt + 1}/3 — {last_err[:160]}[/yellow]")
    if obj is None:
        raise RuntimeError(f"ask_json failed after 3 attempts for {model_cls.__name__}: {last_err}")

    if not _cache_disabled():
        try:
            cache_path.write_text(obj.model_dump_json(), encoding="utf-8")
        except Exception as exc:  # disk full / permission - don't break the run
            console.log(f"[yellow]cache write failed for {key[:8]}: {exc}[/yellow]")
    return obj


def report_cache_stats() -> None:
    if stats.hits == 0 and stats.misses == 0 and stats.bypassed == 0:
        return
    total = stats.hits + stats.misses + stats.bypassed
    pct = (stats.hits / max(stats.hits + stats.misses, 1)) * 100
    console.log(
        f"[bold]llm cache:[/bold] {stats.hits} hit / {stats.misses} miss "
        f"/ {stats.bypassed} bypass  ({pct:.0f}% hit-rate, {total} calls)"
    )
