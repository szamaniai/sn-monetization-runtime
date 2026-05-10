# app/settings.py
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

class Settings:
    """Application configuration loaded from environment variables."""
    APP_NAME: str = "open-bounty-service"
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'bounty.db'}"
    )
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    RADAR_POLL_INTERVAL: int = int(os.getenv("RADAR_POLL_INTERVAL", "300"))
    STACKEXCHANGE_API_KEY: str = os.getenv("STACKEXCHANGE_API_KEY", "")
    STACKEXCHANGE_SITE: str = os.getenv("STACKEXCHANGE_SITE", "math")
    STACKEXCHANGE_ENDPOINT: str = (
        "https://api.stackexchange.com/2.3/questions"
    )
    USER_AGENT: str = "OpenBountyService/1.0"

settings = Settings()