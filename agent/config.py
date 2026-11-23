"""Settings from the environment (and an optional .env file).

Secret values are only read here or in the adapters that hand them to SDK
clients. Nothing prints or logs them; error messages name variables, not values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "schema" / "app_row.schema.json"
APPS_CSV = REPO_ROOT / "data" / "apps.csv"
COVERAGE_CSV = REPO_ROOT / "data" / "composio_coverage.csv"
RUNS_DIR = REPO_ROOT / "data" / "runs"


class ConfigError(RuntimeError):
    """A setting is missing or invalid."""


def load_env() -> None:
    """Load ENV_FILE (or repo-root .env) without overriding variables already set."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # python-dotenv is optional at runtime
        return
    env_file = os.environ.get("ENV_FILE")
    path = Path(env_file).expanduser() if env_file else REPO_ROOT / ".env"
    if env_file and not path.is_file():
        raise ConfigError(f"ENV_FILE points to a file that does not exist: {path}")
    if path.is_file():
        load_dotenv(path, override=False)


def require_env(name: str, why: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigError(f"{why} needs {name} to be set (in the environment or .env)")
    return value


def _float_or_none(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number") from e


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    value = os.environ.get(name, "").strip().lower() or default
    if value not in allowed:
        raise ConfigError(f"{name} must be one of {', '.join(allowed)} (got {value!r})")
    return value


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "bedrock"
    llm_model: str | None = None
    llm_max_tokens: int = 4096
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    search_provider: str = "auto"
    fetch_provider: str = "auto"
    ddgs_backends: tuple[str, ...] = ("yahoo", "duckduckgo", "bing", "brave", "google")
    composio_user_id: str = "composio-app-research"
    http_timeout: float = 20.0
    max_page_chars: int = 20000

    @classmethod
    def from_env(cls) -> Settings:
        backends = os.environ.get("DDGS_BACKENDS", "")
        return cls(
            llm_provider=_choice("LLM_PROVIDER", "bedrock", ("bedrock", "anthropic", "openai", "gemini")),
            llm_model=os.environ.get("LLM_MODEL", "").strip() or None,
            llm_max_tokens=int(os.environ.get("LLM_MAX_TOKENS") or 4096),
            price_in_per_mtok=_float_or_none("LLM_PRICE_IN_PER_MTOK"),
            price_out_per_mtok=_float_or_none("LLM_PRICE_OUT_PER_MTOK"),
            search_provider=_choice(
                "SEARCH_PROVIDER", "auto", ("auto", "composio_exa", "composio_tavily", "ddgs")
            ),
            fetch_provider=_choice("FETCH_PROVIDER", "auto", ("auto", "composio_firecrawl", "http")),
            ddgs_backends=tuple(b.strip() for b in backends.split(",") if b.strip()) or cls.ddgs_backends,
            composio_user_id=os.environ.get("COMPOSIO_USER_ID", "").strip() or cls.composio_user_id,
            http_timeout=float(os.environ.get("HTTP_TIMEOUT") or 20),
            max_page_chars=int(os.environ.get("MAX_PAGE_CHARS") or 20000),
        )

    def est_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        if self.price_in_per_mtok is None or self.price_out_per_mtok is None:
            return None
        cost = input_tokens / 1e6 * self.price_in_per_mtok + output_tokens / 1e6 * self.price_out_per_mtok
        return round(cost, 6)
