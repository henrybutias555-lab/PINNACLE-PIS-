"""Application settings, read from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SECRET = "dev-insecure-secret-change-me"
DEFAULT_ADMIN_PASSWORD = "ChangeMe123!"


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

ON_VERCEL = bool(os.getenv("VERCEL"))


def _encode_password(url: str) -> str:
    """URL-encode the password so characters like '@', '#', '/' or '?' work unescaped.

    Splits on the LAST '@' before the host, so 'user:Pa@ss@host' is read as
    password 'Pa@ss'. Already-encoded passwords (e.g. 'Pa%40ss') are left intact.
    """
    from urllib.parse import quote, unquote

    scheme, sep, rest = url.partition("://")
    if not sep or "@" not in rest:
        return url
    # The host starts after the last '@' (query parameters never contain one in practice).
    at = rest.rfind("@")
    userinfo, hostpart = rest[:at], rest[at + 1:]
    user, colon, password = userinfo.partition(":")
    if not colon:
        return url
    password = quote(unquote(password), safe="")
    return f"{scheme}://{user}:{password}@{hostpart}"


def _database_url() -> str:
    """PIS_DATABASE_URL, else the URL a Vercel Postgres / Neon integration provides."""
    url = (os.getenv("PIS_DATABASE_URL") or os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()
    if not url:
        # Serverless file systems are read-only except /tmp (and /tmp is wiped often).
        return "sqlite:////tmp/pis.db" if ON_VERCEL else "sqlite:///./data/pis.db"
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    url = _encode_password(url)
    # Drop Prisma-style flags (e.g. Supabase's "?pgbouncer=true") that libpq rejects.
    if "?" in url:
        from urllib.parse import parse_qsl, urlencode
        base, query = url.split("?", 1)
        params = [(k, v) for k, v in parse_qsl(query) if k not in ("pgbouncer", "connection_limit", "schema")]
        url = base + ("?" + urlencode(params) if params else "")
    return url


@dataclass
class Settings:
    secret_key: str = field(default_factory=lambda: os.getenv("PIS_SECRET_KEY", DEFAULT_SECRET))
    database_url: str = field(default_factory=_database_url)
    default_currency: str = field(default_factory=lambda: os.getenv("PIS_DEFAULT_CURRENCY", "GHS"))
    admin_email: str = field(default_factory=lambda: os.getenv("PIS_ADMIN_EMAIL", "admin@pinnacle.local"))
    admin_password: str = field(default_factory=lambda: os.getenv("PIS_ADMIN_PASSWORD", DEFAULT_ADMIN_PASSWORD))
    token_minutes: int = field(default_factory=lambda: int(os.getenv("PIS_TOKEN_MINUTES", "480")))
    claude_model: str = field(default_factory=lambda: os.getenv("PIS_CLAUDE_MODEL", "claude-opus-5-5"))
    # Vercel functions reject request bodies over ~4.5 MB.
    max_upload_mb: float = field(default_factory=lambda: float(os.getenv("PIS_MAX_UPLOAD_MB", "4.4" if ON_VERCEL else "40")))
    # Background work is unreliable on serverless, so extract during the upload request there.
    sync_extraction: bool = field(default_factory=lambda: os.getenv("PIS_SYNC_EXTRACTION", "1" if ON_VERCEL else "0") == "1")

    @property
    def ai_enabled(self) -> bool:
        if os.getenv("PIS_DISABLE_AI") == "1":
            return False
        return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))

    def deployment_problems(self) -> list[str]:
        """Settings that must be fixed before a public deployment can safely run."""
        if not ON_VERCEL:
            return []
        problems = []
        if self.secret_key == DEFAULT_SECRET or len(self.secret_key) < 32:
            problems.append("PIS_SECRET_KEY: set a random string of at least 32 characters.")
        if self.admin_password == DEFAULT_ADMIN_PASSWORD:
            problems.append("PIS_ADMIN_PASSWORD: set the password for the first admin account (not the default).")
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL: connect a Postgres database (Vercel Storage → Neon Postgres), "
                            "otherwise all data would be lost whenever Vercel restarts the app.")
        return problems


settings = Settings()
