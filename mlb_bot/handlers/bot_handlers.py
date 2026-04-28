"""
handlers/bot_handlers.py
~~~~~~~~~~~~~~~~~~~~~~~~~
All Telegram command + callback handlers.

Commands:
    /start   – welcome
    /help    – command reference
    /today   – list today's games
    /odds    – show polymarket odds for all today's games
    /analyze – pick a game and get full prediction report
    /analyze_N – shortcut for game N from /today list
"""

from __future__ import annotations

import asyncio
import datetime
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import GCP_PROJECT_ID, MIN_EDGE_PCT, VALUE_EDGE_THRESHOLD
from data.mlb_data import get_todays_games
from data.polymarket import find_value_bets, get_game_odds
from models.predictor import build_profiles_and_predict
from utils.formatter import (
    HELP_MSG,
    LOADING_MSG,
    format_edges,
    format_full_report,
    format_games_list,
    format_polymarket,
    esc,
)

logger = logging.getLogger(__name__)

# ─── In-session game list cache (cleared each day) ────────────────────────────
_session_games: list[dict] = []
_session_date:  str = ""


def _refresh_games() -> list[dict]:
    global _session_games, _session_date
    today = datetime.date.today().isoformat()
    if _session_date != today or not _session_games:
        _session_games = get_todays_games()
        _session_date  = today
    return _session_games


# ─── /start ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    welcome = (
        "⚾ *Welcome to the MLB Prediction Bot\\!*\n\n"
        "I combine *Statcast data*, *Pythagorean math*, *Log5*, and a "
        "*Poisson run\\-scoring model* to give you the best available guess "
        "on who wins — plus live *Polymarket odds* and *value\\-edge alerts*\\.\n\n"
        "Use /today to see today's slate, then /analyze to dive in\\.\n\n"
        "Type /help for all commands\\."
    )
    await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /help ────────────────────────────────────────────────────────────────────

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_MSG, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /today ───────────────────────────────────────────────────────────────────

async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📡 _Fetching today's schedule\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    games = _refresh_games()
    msg   = format_games_list(games)
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)


# ─── /analyze (interactive picker) ───────────────────────────────────────────

async def cmd_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Check for /analyze_N shortcut
    text = update.message.text or ""
    if "_" in text:
        try:
            idx = int(text.split("_")[-1]) - 1
            await _run_analysis(update, ctx, idx)
            return
        except (ValueError, IndexError):
            pass

    games = _refresh_games()
    if not games:
        await update.message.reply_text(
            "⚾ No games found for today\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    buttons = []
    for i, g in enumerate(games):
        label = f"{g['away_team']} @ {g['home_team']}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"analyze:{i}")])

    await update.message.reply_text(
        "⚾ *Pick a game to analyze:*",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ─── /odds (all games, market-only, no prediction) ───────────────────────────

async def cmd_odds(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "💰 _Fetching Polymarket odds for today's games\\.\\.\\._",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    games = _refresh_games()
    if not games:
        await update.message.reply_text(
            "⚾ No games scheduled today\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    for g in games:
        odds   = get_game_odds(g["away_team"], g["home_team"])
        header = f"⚾ *{esc(g['away_team'])} \\@ {esc(g['home_team'])}*\n"
        body   = format_polymarket(odds, g["away_team"], g["home_team"])
        try:
            await update.message.reply_text(
                header + body, parse_mode=ParseMode.MARKDOWN_V2,
                disable_web_page_preview=True,
            )
        except Exception as exc:
            logger.warning("Odds message send error: %s", exc)
            await update.message.reply_text(
                f"⚾ {g['away_team']} @ {g['home_team']}: odds unavailable"
            )
        await asyncio.sleep(0.3)   # avoid flood limits


# ─── Callback: inline button press ───────────────────────────────────────────

async def cb_analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    try:
        idx = int(query.data.split(":")[-1])
    except (ValueError, IndexError):
        return
    # Acknowledge selection
    games = _refresh_games()
    if idx < len(games):
        g = games[idx]
        await query.edit_message_text(
            f"⚙️ Analyzing *{esc(g['away_team'])} \\@ {esc(g['home_team'])}*\\.\\.\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    await _run_analysis_from_query(query, ctx, idx)


# ─── Core analysis runner ─────────────────────────────────────────────────────

async def _run_analysis(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    idx: int,
) -> None:
    games = _refresh_games()
    if idx < 0 or idx >= len(games):
        await update.message.reply_text(
            f"❌ Game \\#{idx+1} not found\\. Use /today to see the list\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    msg = await update.message.reply_text(
        LOADING_MSG, parse_mode=ParseMode.MARKDOWN_V2
    )
    game = games[idx]
    report, _ = await _build_report(game)

    try:
        await msg.edit_text(
            report,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Edit message failed: %s", exc)
        # Fall back to new message
        await update.message.reply_text(
            report,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )


async def _run_analysis_from_query(
    query,
    ctx: ContextTypes.DEFAULT_TYPE,
    idx: int,
) -> None:
    games = _refresh_games()
    if idx < 0 or idx >= len(games):
        await query.message.reply_text(
            "❌ Game not found\\.", parse_mode=ParseMode.MARKDOWN_V2
        )
        return

    game   = games[idx]
    report, _ = await _build_report(game)

    try:
        await query.message.reply_text(
            report,
            parse_mode=ParseMode.MARKDOWN_V2,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error("Send after callback failed: %s", exc)
        # Fallback: strip complex formatting
        await query.message.reply_text(report[:4000])


# ─── The actual heavy computation (offloaded to thread) ──────────────────────

async def _build_report(game: dict) -> tuple[str, dict]:
    """Run prediction + odds in a thread pool to avoid blocking the event loop."""
    loop = asyncio.get_event_loop()

    away   = game["away_team"]
    home   = game["home_team"]
    away_p = game.get("away_probable_pitcher", "TBD") or "TBD"
    home_p = game.get("home_probable_pitcher", "TBD") or "TBD"
    gtime  = game.get("game_time", "")
    venue  = game.get("venue", "")

    season = datetime.date.today().year

    # Run blocking IO/compute in executor
    def _compute():
        pred  = build_profiles_and_predict(away, home, away_p, home_p, season)
        odds  = get_game_odds(away, home)
        edges = find_value_bets(
            away, home,
            pred.win_prob_away,
            pred.win_prob_home,
            pred.total_runs_expected,
            min_edge=VALUE_EDGE_THRESHOLD,
        )
        return pred, odds, edges

    pred, odds, edges = await loop.run_in_executor(None, _compute)

    report = format_full_report(
        pred, odds, edges,
        game_time=gtime,
        venue=venue,
        away_pitcher=away_p,
        home_pitcher=home_p,
    )
    return report, {"pred": pred, "odds": odds, "edges": edges}


# ─── Dynamic /analyze_N handlers ─────────────────────────────────────────────

def _make_analyze_n_handler(idx: int):
    async def handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        await _run_analysis(update, ctx, idx)
    return handler


# ─── Registration ─────────────────────────────────────────────────────────────

def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("today",   cmd_today))
    app.add_handler(CommandHandler("odds",    cmd_odds))
    app.add_handler(CommandHandler("analyze", cmd_analyze))

    # /analyze_1 through /analyze_16 (max 16 games in a day)
    for i in range(1, 17):
        app.add_handler(
            CommandHandler(f"analyze_{i}", _make_analyze_n_handler(i - 1))
        )

    app.add_handler(CallbackQueryHandler(cb_analyze, pattern=r"^analyze:\d+$"))
