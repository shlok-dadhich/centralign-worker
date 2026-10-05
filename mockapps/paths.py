"""Shared filesystem locations for the two mock-app databases."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PORTAL_DB = DATA / "portal.db"
FINANCE_DB = DATA / "finance.db"
