"""Strict, non-interactive VPS configuration. Never print secrets."""
from dataclasses import dataclass
import os
from pathlib import Path


def number(name: str, default: int, low: int, high: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be in range {low}..{high}")
    return value


@dataclass(frozen=True)
class Settings:
    database_url: str
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_path: str
    channels: tuple[str, ...]
    provider: str
    api_key: str
    model: str
    deep_model: str
    monthly_budget_usd: int
    max_output_tokens: int
    prompt_chars_per_message: int
    batch_size: int
    initial_history: int
    sync_seconds: int
    deep_max_per_day: int
    dashboard_password: str
    dashboard_user: str

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv(override=False)
        except ImportError:
            pass
        db_url = os.getenv("DATABASE_URL", "").strip()
        if not db_url:
            # Construct driver URL safely: passwords with special chars are escaped.
            from sqlalchemy.engine import URL
            db_url = URL.create(
                "postgresql+psycopg",
                username=os.getenv("DB_USER", "monitor"),
                password=os.getenv("DB_PASSWORD", ""),
                host=os.getenv("DB_HOST", "postgres"),
                port=int(os.getenv("DB_PORT", "5432")),
                database=os.getenv("DB_NAME", "monitor"),
            ).render_as_string(hide_password=False)
        config_path = os.getenv("CLOUD_CONFIG_PATH", "configs/armenia_groups_osint_pro.json")
        import json
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        channels = tuple(dict.fromkeys(ch["username"] for block in config["channels"].values() for ch in block))
        provider = os.getenv("CLOUD_PROVIDER", "dry_run").strip().lower()
        if provider not in {"dry_run", "openai"}:
            raise ValueError("CLOUD_PROVIDER must be dry_run or openai")
        secret = os.getenv("OPENAI_API_KEY", "").strip()
        if provider == "openai" and not secret:
            raise ValueError("OPENAI_API_KEY required when CLOUD_PROVIDER=openai")
        return cls(
            database_url=db_url,
            telegram_api_id=number("TG_API_ID", 0, 0, 2147483647),
            telegram_api_hash=os.getenv("TG_API_HASH", ""),
            telegram_session_path=os.getenv("TG_SESSION_PATH", "/run/telegram_sessions/telegram_monitor"),
            channels=channels,
            provider=provider,
            api_key=secret,
            model=os.getenv("CLOUD_MODEL", "gpt-6-luna"),
            deep_model=os.getenv("CLOUD_DEEP_MODEL", "gpt-6.1-sol"),
            monthly_budget_usd=number("CLOUD_BUDGET_USD", 27, 1, 10000),
            max_output_tokens=number("CLOUD_MAX_OUTPUT_TOKENS", 2600, 256, 4000),
            prompt_chars_per_message=number("CLOUD_TEXT_CHARS", 1500, 100, 5000),
            batch_size=number("CLOUD_BATCH_SIZE", 20, 1, 30),
            initial_history=number("CLOUD_INITIAL_HISTORY", 100, 1, 2000),
            sync_seconds=number("CLOUD_SYNC_SECONDS", 90, 15, 3600),
            deep_max_per_day=number("CLOUD_DEEP_MAX_PER_DAY", 50, 0, 2000),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            dashboard_user=os.getenv("DASHBOARD_USER", "admin"),
        )
