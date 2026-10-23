import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

# Loaded before the pipeline config so OLLAMA_HOST in .env takes effect.
load_dotenv(BACKEND_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    reload: bool
    cors_origins: list[str]
    frontend_dir: Path
    documents_dir: Path
    sql_dir: Path  # saved database connections (they hold passwords)
    workspaces_dir: Path  # workspaces (Knowledge): documents and their search index
    chatfiles_dir: Path  # files attached in the chat, kept for documents.keep_hours
    skills_dir: Path  # which built-in skills are on, and each user's memories
    auth_db: Path  # users database (SQLite)
    auth_secret: str  # signs login tokens; empty = a random one is kept in data/auth_secret
    token_hours: float
    admin_email: str  # with admin_password: makes the first admin when there are no users
    admin_password: str
    access_key: str  # shared with the n8n access-request workflow; empty = /api/access is off
    pipeline: str  # "ollama" (default) or "llamacpp": which model server the whole app uses


settings = Settings(
    host=os.environ.get("HOST", "127.0.0.1"),
    port=int(os.environ.get("PORT", "8000")),
    reload=os.environ.get("RELOAD", "false").lower() in {"1", "true", "yes"},
    cors_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()],
    frontend_dir=Path(os.environ.get("FRONTEND_DIR") or BACKEND_DIR.parent / "frontend"),
    documents_dir=Path(os.environ.get("DOCUMENTS_DIR") or BACKEND_DIR / "data" / "documents"),
    sql_dir=Path(os.environ.get("SQL_DIR") or BACKEND_DIR / "data" / "sql"),
    workspaces_dir=Path(os.environ.get("WORKSPACES_DIR") or BACKEND_DIR / "data" / "workspaces"),
    chatfiles_dir=Path(os.environ.get("CHATFILES_DIR") or BACKEND_DIR / "data" / "chatfiles"),
    skills_dir=Path(os.environ.get("SKILLS_DIR") or BACKEND_DIR / "data" / "skills"),
    auth_db=Path(os.environ.get("AUTH_DB") or BACKEND_DIR / "data" / "auth.db"),
    auth_secret=os.environ.get("AUTH_SECRET", ""),
    token_hours=float(os.environ.get("TOKEN_HOURS", "168")),
    admin_email=os.environ.get("ADMIN_EMAIL", ""),
    admin_password=os.environ.get("ADMIN_PASSWORD", ""),
    access_key=os.environ.get("ACCESS_API_KEY", "").strip(),
    pipeline=os.environ.get("PIPELINE", "ollama").strip().lower(),
)
