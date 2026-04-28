"""
config.py – Central configuration loader.
Reads from .env (or environment variables already exported).
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Google Cloud / BigQuery ───────────────────────────────────
GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

# ── Polymarket ────────────────────────────────────────────────
POLYMARKET_API_KEY: str = os.getenv("POLYMARKET_API_KEY", "")
POLY_GAMMA_BASE  = "https://gamma-api.polymarket.com"
POLY_CLOB_BASE   = "https://clob.polymarket.com"

# ── Model knobs ───────────────────────────────────────────────
VALUE_EDGE_THRESHOLD: float = float(os.getenv("VALUE_EDGE_THRESHOLD", "0.05"))
MIN_EDGE_PCT: float          = float(os.getenv("MIN_EDGE_PCT", "3"))

# Home-field advantage baseline (historical ~54 %)
HOME_FIELD_BOOST: float = 0.04

# Seasons of history to pull for team stats
HISTORY_SEASONS: int = 3
