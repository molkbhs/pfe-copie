"""
Database configuration — loaded exclusively from environment variables.
Copy .env.example to .env and fill in your values before starting the server.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the backend directory (or any parent)
_backend_dir = Path(__file__).resolve().parent
load_dotenv(_backend_dir / ".env")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


DB_CONFIG = {
    "host":     os.getenv("DB_HOST",     "localhost"),
    "port":     _env_int("DB_PORT",      3307),
    "user":     os.getenv("DB_USER",     "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME",     "pfe_bd"),
}
