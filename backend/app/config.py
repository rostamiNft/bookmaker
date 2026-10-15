"""Application settings. Everything is configurable through environment variables
(or a `.env` file in the repository root). No secrets live in source code."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    env: str = field(default_factory=lambda: os.environ.get("BOOKMAKER_ENV", "development"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("BOOKMAKER_DATA_DIR", REPO_ROOT / "data")))
    # Optional single shared API token. When set, every /api request must send
    # `Authorization: Bearer <token>` (or ?token= for downloads opened in a new tab).
    api_token: str = field(default_factory=lambda: os.environ.get("BOOKMAKER_API_TOKEN", ""))
    # none | token | accounts. Default: "token" when BOOKMAKER_API_TOKEN is set, otherwise "none".
    auth_mode_env: str = field(default_factory=lambda: os.environ.get("BOOKMAKER_AUTH_MODE", "").strip().lower())
    session_days: int = field(default_factory=lambda: _int("BOOKMAKER_SESSION_DAYS", 14))
    # Key used to encrypt provider API keys at rest. If empty a key file is generated in data_dir.
    secret_key: str = field(default_factory=lambda: os.environ.get("BOOKMAKER_SECRET_KEY", ""))
    max_concurrent_books: int = field(default_factory=lambda: _int("BOOKMAKER_MAX_CONCURRENT_BOOKS", 1))
    max_concurrent_images: int = field(default_factory=lambda: _int("BOOKMAKER_MAX_CONCURRENT_IMAGES", 2))
    max_upload_mb: int = field(default_factory=lambda: _int("BOOKMAKER_MAX_UPLOAD_MB", 10))
    max_disk_gb: int = field(default_factory=lambda: _int("BOOKMAKER_MAX_DISK_GB", 20))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("BOOKMAKER_RATE_LIMIT_PER_MINUTE", 240))
    enable_ocr: bool = field(default_factory=lambda: _bool("BOOKMAKER_ENABLE_OCR", True))
    cors_origins: str = field(default_factory=lambda: os.environ.get("BOOKMAKER_CORS_ORIGINS", ""))
    start_worker: bool = field(default_factory=lambda: _bool("BOOKMAKER_START_WORKER", True))

    @property
    def auth_mode(self) -> str:
        """none | token | accounts. Unset → "token" when an API token is configured, else "none"."""
        if self.auth_mode_env in ("none", "token", "accounts"):
            return self.auth_mode_env
        return "token" if self.api_token else "none"

    @auth_mode.setter
    def auth_mode(self, value: str):
        self.auth_mode_env = value

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "bookmaker.db"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.projects_dir, self.trash_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)


FONTS_DIR = REPO_ROOT / "assets" / "fonts"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"

settings = Settings()
settings.ensure_dirs()
