"""
main.py – MLB Prediction Telegram Bot entry point.

Run with:
    python main.py

Requirements:
    pip install -r requirements.txt
    Copy .env.example → .env and fill in your values
"""

import logging
import sys

from telegram import BotCommand
from telegram.ext import Application

from config import TELEGRAM_BOT_TOKEN
from handlers.bot_handlers import register_handlers

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log"),
    ],
)
# Quieten noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("pybaseball").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ─── Bot commands menu ────────────────────────────────────────────────────────

COMMANDS = [
    BotCommand("start",   "👋 Welcome & intro"),
    BotCommand("today",   "📅 Show today's MLB games"),
    BotCommand("analyze", "🔍 Analyze & predict a game"),
    BotCommand("odds",    "💰 Show live Polymarket odds"),
    BotCommand("help",    "❓ Command reference"),
]


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is not set. Check your .env file.")
        sys.exit(1)

    logger.info("⚾  Starting MLB Prediction Bot...")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )

    # Register all command & callback handlers
    register_handlers(app)

    # Set bot commands in Telegram's menu
    async def post_init(application: Application) -> None:
        await application.bot.set_my_commands(COMMANDS)
        me = await application.bot.get_me()
        logger.info("✅ Bot online: @%s  (id=%s)", me.username, me.id)

    app.post_init = post_init

    logger.info("🚀 Polling for updates — press Ctrl+C to stop")
    app.run_polling(
        drop_pending_updates=True,
        poll_interval=1.0,
    )


if __name__ == "__main__":
    main()
