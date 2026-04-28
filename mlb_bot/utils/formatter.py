"""
utils/formatter.py
~~~~~~~~~~~~~~~~~~~
Formats GamePrediction + Polymarket odds into beautiful Telegram MarkdownV2 messages.
All special characters are escaped per Telegram spec.
"""

from __future__ import annotations
import re
from models.predictor import GamePrediction


# ─── Telegram MarkdownV2 escape ───────────────────────────────────────────────

def esc(text: str) -> str:
    """Escape all MarkdownV2 reserved characters."""
    reserved = r'\_*[]()~`>#+-=|{}.!'
    return re.sub(f"([{re.escape(reserved)}])", r"\\\1", str(text))


def pct(v: float) -> str:
    return f"{v*100:.1f}%"


def prob_bar(p: float, width: int = 10) -> str:
    """Visual probability bar using block chars."""
    filled = round(p * width)
    return "█" * filled + "░" * (width - filled)


# ─── Confidence badge ─────────────────────────────────────────────────────────

def confidence_badge(conf: float) -> str:
    if conf >= 75:
        return "🟢 HIGH"
    elif conf >= 65:
        return "🟡 MEDIUM"
    else:
        return "🔴 SPECULATIVE"


# ─── American odds helper ─────────────────────────────────────────────────────

def format_american(odds_str: str) -> str:
    """Add colour hint to odds string."""
    if not odds_str or odds_str == "N/A":
        return "N/A"
    if odds_str.startswith("+"):
        return f"\\+{odds_str[1:]}"   # escape the +
    return esc(odds_str)


# ─── Game header ─────────────────────────────────────────────────────────────

def format_game_header(pred: GamePrediction, game_time: str = "", venue: str = "") -> str:
    winner      = pred.winner
    winner_prob = pred.winner_prob
    loser       = pred.away_team if winner == pred.home_team else pred.home_team

    away_bar = prob_bar(pred.win_prob_away)
    home_bar = prob_bar(pred.win_prob_home)

    time_line  = f"\n🕐 {esc(game_time)}" if game_time else ""
    venue_line = f"  📍 {esc(venue)}"     if venue     else ""

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"⚾ *{esc(pred.away_team)} \\@ {esc(pred.home_team)}*",
        f"{esc(time_line)}{esc(venue_line)}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "🏆 *PREDICTED WINNER*",
        f"   🎯 *{esc(winner)}* — {esc(pct(winner_prob))} probability",
        f"   {esc(confidence_badge(pred.confidence_pct))}  confidence: *{esc(str(pred.confidence_pct))}%*",
        f"   _\\(±{esc(str(int(pred.confidence_interval)))} percentage points\\)_",
        "",
        "📊 *WIN PROBABILITY*",
        f"   {esc(pred.away_team[:16])}",
        f"   {esc(away_bar)}  {esc(pct(pred.win_prob_away))}",
        f"   {esc(pred.home_team[:16])}",
        f"   {esc(home_bar)}  {esc(pct(pred.win_prob_home))}",
    ]
    return "\n".join(lines)


# ─── Score / runs section ─────────────────────────────────────────────────────

def format_score_section(pred: GamePrediction) -> str:
    total = pred.total_runs_expected
    lines = [
        "",
        "🔢 *PROJECTED SCORE*",
        f"   {esc(pred.away_team)}: *{pred.predicted_score_away}*  "
        f"  {esc(pred.home_team)}: *{pred.predicted_score_home}*",
        "",
        "📈 *EXPECTED RUN TOTALS*",
        f"   {esc(pred.away_team)}: {esc(f'{pred.expected_runs_away:.2f}')} runs",
        f"   {esc(pred.home_team)}: {esc(f'{pred.expected_runs_home:.2f}')} runs",
        f"   Combined O/U line: *{esc(f'{total:.1f}')} runs*",
    ]
    return "\n".join(lines)


# ─── Analysis bullets ─────────────────────────────────────────────────────────

def format_analysis(pred: GamePrediction) -> str:
    lines = ["", "🔬 *MODEL ANALYSIS*"]
    for bullet in pred.explanation:
        # strip leading emoji if already has one
        lines.append(f"   {esc(bullet)}")
    return "\n".join(lines)


# ─── Polymarket odds ──────────────────────────────────────────────────────────

def format_polymarket(odds: dict, away: str, home: str) -> str:
    lines = ["", "💰 *POLYMARKET LIVE ODDS*"]

    ml = odds.get("moneyline")
    if ml:
        lines += [
            "   *Moneyline*",
            f"   {esc(away)}: {format_american(ml['away_american'])}  "
            f"\\({esc(pct(ml['away_prob']))}\\)",
            f"   {esc(home)}: {format_american(ml['home_american'])}  "
            f"\\({esc(pct(ml['home_prob']))}\\)",
            f"   _Vol: \\${esc(f'{ml[\"volume\"]:,.0f}')} USDC_",
        ]
    else:
        lines.append("   _No moneyline market found_")

    tot = odds.get("total")
    if tot:
        line_str = f"{tot['line']} runs" if tot.get("line") else "line TBD"
        lines += [
            "",
            f"   *Total Runs \\({esc(line_str)}\\)*",
            f"   Over:  {format_american(tot['over_american'])}  "
            f"\\({esc(pct(tot['over_prob']))}\\)",
            f"   Under: {format_american(tot['under_american'])}  "
            f"\\({esc(pct(tot['under_prob']))}\\)",
        ]
    else:
        lines.append("\n   _No total\\-runs market found_")

    rl = odds.get("runline")
    if rl:
        lines += [
            "",
            f"   *Run Line \\({esc(str(rl['line']))}\\)*",
            f"   {esc(rl['favorite'])}: {format_american(rl['fav_american'])}  "
            f"\\({esc(pct(rl['fav_prob']))}\\)",
        ]

    if odds.get("markets_found", 0) == 0:
        lines = ["", "💰 *POLYMARKET*", "   _No active MLB markets found for this game\\._",
                 "   _Markets may open closer to game time\\._"]

    return "\n".join(lines)


# ─── Value edges ─────────────────────────────────────────────────────────────

def format_edges(edges: list[dict]) -> str:
    if not edges:
        return "\n\n✖️ *NO SIGNIFICANT VALUE EDGE DETECTED*\n   _Model aligns with market pricing\\._"

    lines = ["", "", "🚨 *VALUE EDGE ALERT* 🚨"]
    for e in edges:
        edge_pct = e["edge"] * 100
        stars = "⭐" * min(int(edge_pct / 2) + 1, 5)
        lines += [
            f"   {stars} *{esc(e['market_type'])} — {esc(e['side'])}*",
            f"   Market: {format_american(e['american'])}  "
            f"\\({esc(pct(e['market_prob']))}\\)",
            f"   Model:  {esc(pct(e['model_prob']))}",
            f"   Edge:   *\\+{esc(f'{edge_pct:.1f}')}%* in your favour",
            f"   _[View on Polymarket]({esc(e['url'])})_",
            "",
        ]
    lines.append("   ⚠️ _Past performance ≠ future results\\. Bet responsibly\\._")
    return "\n".join(lines)


# ─── Full game report ─────────────────────────────────────────────────────────

def format_full_report(
    pred: GamePrediction,
    odds: dict,
    edges: list[dict],
    game_time: str = "",
    venue: str = "",
    away_pitcher: str = "",
    home_pitcher: str = "",
) -> str:
    pitcher_line = ""
    if away_pitcher or home_pitcher:
        ap = away_pitcher if away_pitcher and away_pitcher.upper() != "TBD" else "TBD"
        hp = home_pitcher if home_pitcher and home_pitcher.upper() != "TBD" else "TBD"
        pitcher_line = (
            f"\n\n🤾 *PROBABLE STARTERS*\n"
            f"   {esc(pred.away_team)}: {esc(ap)}\n"
            f"   {esc(pred.home_team)}: {esc(hp)}"
        )

    report = (
        format_game_header(pred, game_time, venue)
        + pitcher_line
        + format_score_section(pred)
        + format_analysis(pred)
        + format_polymarket(odds, pred.away_team, pred.home_team)
        + format_edges(edges)
        + "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        + f"\n_Powered by Statcast \\+ Log5 \\+ Poisson Model_"
        + f"\n_⚠️ For entertainment only\\. Not financial advice\\._"
    )
    return report


# ─── Today's games list ───────────────────────────────────────────────────────

def format_games_list(games: list[dict]) -> str:
    if not games:
        return "⚾ No MLB games scheduled today\\."

    lines = [
        "⚾ *TODAY'S MLB GAMES*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, g in enumerate(games, 1):
        status = g.get("status", "")
        time_raw = g.get("game_time", "")
        # Format time (strip microseconds)
        try:
            from datetime import datetime, timezone
            import pytz
            dt = datetime.fromisoformat(time_raw.replace("Z", "+00:00"))
            et = dt.astimezone(pytz.timezone("America/New_York"))
            time_fmt = et.strftime("%-I:%M %p ET")
        except Exception:
            time_fmt = time_raw[:16] if time_raw else "TBD"

        ap = g.get("away_probable_pitcher", "TBD") or "TBD"
        hp = g.get("home_probable_pitcher", "TBD") or "TBD"

        lines += [
            f"*{i}\\. {esc(g['away_team'])} \\@ {esc(g['home_team'])}*",
            f"   🕐 {esc(time_fmt)}  📍 {esc(g.get('venue',''))}",
            f"   ⚾ {esc(ap)} vs {esc(hp)}",
            f"   👉 /analyze\\_{i}",
            "",
        ]
    lines.append("_Use /analyze to pick a game by number_")
    return "\n".join(lines)


# ─── Loading message ──────────────────────────────────────────────────────────

LOADING_MSG = (
    "⚙️ *Crunching the numbers\\.\\.\\.*\n\n"
    "📡 Fetching Statcast data\\.\\.\n"
    "🧮 Running Pythagorean \\+ Log5 model\\.\\.\n"
    "🎲 Solving Poisson distribution\\.\\.\n"
    "💰 Checking Polymarket odds\\.\\.\n\n"
    "_This takes 10\\-30 seconds_"
)

# ─── Help message ─────────────────────────────────────────────────────────────

HELP_MSG = """
⚾ *MLB PREDICTION BOT*
━━━━━━━━━━━━━━━━━━━━━━━━━━━

*COMMANDS*

/today — Show all games scheduled today
/analyze — Interactively pick a game to analyze
/analyze\\_1 — Analyze game \\#1 from /today list
/odds — Show live Polymarket odds for all today's games
/help — This message

*HOW IT WORKS*

The bot uses a 6\\-layer model:
① Pythagorean Win\\% from season RS/RA
② Log5 formula for true matchup probability
③ Pitcher Statcast adjustment \\(xFIP/FIP\\)
④ Independent Poisson run\\-scoring model
⑤ Recent form \\(last 10 games\\)
⑥ Head\\-to\\-head historical record

All predictions capped at *80% confidence* per your spec\\.

*POLYMARKET EDGE DETECTION*

The bot compares its model probability against live Polymarket markets and flags any bets where your edge is ≥3%\\.

Markets checked: *Moneyline, Total Runs \\(O/U\\), Run Line*

⚠️ _For entertainment only\\. Not financial advice\\._
"""
