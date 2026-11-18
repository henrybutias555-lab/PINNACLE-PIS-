"""Application settings, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass
class Settings:
    secret_key: str = field(default_factory=lambda: os.getenv("PIS_SECRET_KEY", "dev-insecure-secret-change-me"))
    database_url: str = field(default_factory=lambda: os.getenv("PIS_DATABASE_URL", "sqlite:///./data/pis.db"))
    upload_dir: Path = field(default_factory=lambda: Path(os.getenv("PIS_UPLOAD_DIR", "./data/uploads")))
    default_currency: str = field(default_factory=lambda: os.getenv("PIS_DEFAULT_CURRENCY", "GHS"))
    admin_email: str = field(default_factory=lambda: os.getenv("PIS_ADMIN_EMAIL", "admin@pinnacle.local"))
    admin_password: str = field(default_factory=lambda: os.getenv("PIS_ADMIN_PASSWORD", "ChangeMe123!"))
    token_minutes: int = field(default_factory=lambda: int(os.getenv("PIS_TOKEN_MINUTES", "480")))
    claude_model: str = field(default_factory=lambda: os.getenv("PIS_CLAUDE_MODEL", "claude-opus-5-5"))
    max_upload_mb: int = field(default_factory=lambda: int(os.getenv("PIS_MAX_UPLOAD_MB", "40")))

    @property
    def ai_enabled(self) -> bool:
        if os.getenv("PIS_DISABLE_AI") == "1":
            return False
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


settings = Settings()
