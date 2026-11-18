"""Settings, loaded from environment variables (and a .env file if present)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "leads.db"
OUTBOX_DIR = DATA_DIR / "outbox"
PROFILE_PATH = ROOT / "config" / "business_profile.md"

MODEL = os.getenv("LEADAGENT_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("LEADAGENT_EFFORT", "high")

MAIL_MODE = os.getenv("MAIL_MODE", "dry_run")  # dry_run | graph
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "admin@pdesigns.com.au")
MS_TENANT_ID = os.getenv("MS_TENANT_ID", "")
MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "")
MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "")

MAX_SENDS_PER_DAY = int(os.getenv("MAX_SENDS_PER_DAY", "15"))
FOLLOWUP_AFTER_DAYS = int(os.getenv("FOLLOWUP_AFTER_DAYS", "7"))
MAX_FOLLOWUPS = int(os.getenv("MAX_FOLLOWUPS", "2"))
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5050"))


def business_profile() -> str:
    return PROFILE_PATH.read_text(encoding="utf-8")
