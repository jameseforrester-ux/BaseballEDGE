"""
data/polymarket.py
~~~~~~~~~~~~~~~~~~
Pulls current MLB market odds from Polymarket's public Gamma API.

Polymarket structures each binary outcome as a CLOB with two sides:
    YES token (win) → implied probability = last price
    NO  token (loss) → 1 - YES price

We surface:
  • Moneyline (game winner)
  • Total Runs (Over/Under markets)
  • Run-line / spread markets (where listed)

All prices are fractional (0–1). We convert to American odds for display.
No API key is required for read-only market data.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"

HEADERS = {
    "Accept":     "application/json",
    "User-Agent": "MLB-TelegramBot/1.0",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def prob_to_american(p: float) -> str:
    """Convert implied probability (0-1) to American moneyline string."""
    if p <= 0 or p >= 1:
        return "N/A"
    if p >= 0.5:
        return f"-{round((p / (1 - p)) * 100)}"
    else:
        return f"+{round(((1 - p) / p) * 100)}"


def american_to_prob(american: int) -> float:
    """Convert American odds to implied probability."""
    if american > 0:
        return 100 / (american + 100)
    else:
        return abs(american) / (abs(american) + 100)


# ─── Fetchers ────────────────────────────────────────────────────────────────

def _fetch_gamma_markets(tag_slug: str = "mlb", limit: int = 100) -> list[dict]:
    """Fetch active Polymarket markets tagged with a given slug."""
    try:
        resp = requests.get(
            f"{GAMMA_BASE}/markets",
            params={
                "active":    "true",
                "closed":    "false",
                "tag_slug":  tag_slug,
                "limit":     limit,
            },
            headers=HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        # API may return list or dict with 'markets' key
        if isinstance(data, list):
            return data
        return data.get("markets", [])
    except Exception as exc:
        logger.warning("Polymarket Gamma fetch error: %s", exc)
        return []


def _fetch_clob_orderbook(condition_id: str) -> Optional[dict]:
    """Fetch the CLOB orderbook for a specific condition to get mid-price."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/book",
            params={"token_id": condition_id},
            headers=HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ─── Market parsing ───────────────────────────────────────────────────────────

def _extract_teams_from_title(title: str) -> tuple[str, str]:
    """
    Try to parse 'Away @ Home' or 'Team A vs Team B' patterns.
    Returns (team_a, team_b) or ('', '').
    """
    # Pattern: "New York Yankees @ Boston Red Sox"
    m = re.search(r"^(.+?)\s+(?:@|vs\.?)\s+(.+?)(?:\s*[-–]|$)", title, re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", ""


def _classify_market(title: str) -> str:
    """Classify a market as 'moneyline', 'total', 'runline', or 'other'."""
    t = title.lower()
    if any(kw in t for kw in ["over", "under", "total runs", "o/u"]):
        return "total"
    if any(kw in t for kw in ["+1.5", "-1.5", "run line", "runline", "spread"]):
        return "runline"
    if any(kw in t for kw in ["win", "winner", "beat", "defeats", "series"]):
        return "moneyline"
    # Default: if it mentions two teams, likely moneyline
    a, b = _extract_teams_from_title(title)
    if a and b:
        return "moneyline"
    return "other"


# ─── Public interface ─────────────────────────────────────────────────────────

def get_mlb_markets() -> list[dict]:
    """
    Return all active MLB Polymarket markets, enriched with:
        title, market_type, team_a, team_b,
        yes_price, no_price, american_yes, american_no,
        volume, condition_id, url
    """
    raw = _fetch_gamma_markets("mlb")
    if not raw:
        # Try baseball tag as fallback
        raw = _fetch_gamma_markets("baseball")

    enriched = []
    for m in raw:
        title        = m.get("question", m.get("title", ""))
        condition_id = m.get("conditionId", m.get("condition_id", ""))
        yes_price    = float(m.get("outcomePrices", [0.5, 0.5])[0] if
                            isinstance(m.get("outcomePrices"), list) else 0.5)
        no_price     = 1.0 - yes_price
        volume       = float(m.get("volume", 0) or 0)

        team_a, team_b = _extract_teams_from_title(title)
        mtype          = _classify_market(title)

        enriched.append({
            "title":        title,
            "market_type":  mtype,
            "team_a":       team_a,
            "team_b":       team_b,
            "yes_price":    yes_price,
            "no_price":     no_price,
            "american_yes": prob_to_american(yes_price),
            "american_no":  prob_to_american(no_price),
            "volume":       volume,
            "condition_id": condition_id,
            "url":          m.get("url", f"https://polymarket.com/event/{m.get('slug', '')}"),
        })

    return enriched


def get_game_odds(away_team: str, home_team: str) -> dict:
    """
    Retrieve Polymarket odds for a specific matchup.
    Searches by team name substring match across all active MLB markets.

    Returns:
        {
          moneyline: {away_prob, home_prob, away_american, home_american, volume, url},
          total:     {line, over_prob, under_prob, over_american, under_american, url},
          runline:   {favorite, line, fav_prob, dog_prob, url},
        }
    """
    markets = get_mlb_markets()

    away_lower = away_team.lower()
    home_lower = home_team.lower()

    result: dict = {
        "moneyline": None,
        "total":     None,
        "runline":   None,
        "markets_found": 0,
    }

    def teams_match(m: dict) -> bool:
        ta = m["team_a"].lower()
        tb = m["team_b"].lower()
        title = m["title"].lower()
        # Check both orderings
        fwd = (any(w in ta for w in away_lower.split()) and
               any(w in tb for w in home_lower.split()))
        rev = (any(w in ta for w in home_lower.split()) and
               any(w in tb for w in away_lower.split()))
        # Also check raw title
        name_in_title = (any(w in title for w in away_lower.split()[-1:]) and
                         any(w in title for w in home_lower.split()[-1:]))
        return fwd or rev or name_in_title

    for m in markets:
        if not teams_match(m):
            continue
        result["markets_found"] += 1
        mtype = m["market_type"]

        if mtype == "moneyline" and result["moneyline"] is None:
            # Determine which price corresponds to away vs home
            ta = m["team_a"].lower()
            away_is_yes = any(w in ta for w in away_lower.split())
            away_prob   = m["yes_price"] if away_is_yes else m["no_price"]
            home_prob   = 1.0 - away_prob
            result["moneyline"] = {
                "away_prob":     away_prob,
                "home_prob":     home_prob,
                "away_american": prob_to_american(away_prob),
                "home_american": prob_to_american(home_prob),
                "volume":        m["volume"],
                "url":           m["url"],
                "title":         m["title"],
            }

        elif mtype == "total" and result["total"] is None:
            # Try to extract the line (e.g. "Over 8.5 Runs")
            line_match = re.search(r"(\d+\.?\d*)\s*run", m["title"], re.IGNORECASE)
            line = float(line_match.group(1)) if line_match else None
            result["total"] = {
                "line":          line,
                "over_prob":     m["yes_price"],
                "under_prob":    m["no_price"],
                "over_american": m["american_yes"],
                "under_american":m["american_no"],
                "volume":        m["volume"],
                "url":           m["url"],
                "title":         m["title"],
            }

        elif mtype == "runline" and result["runline"] is None:
            line_match = re.search(r"([+-]\d+\.?\d*)", m["title"])
            line = float(line_match.group(1)) if line_match else -1.5
            result["runline"] = {
                "favorite":  m["team_a"],
                "line":      line,
                "fav_prob":  m["yes_price"],
                "dog_prob":  m["no_price"],
                "fav_american": m["american_yes"],
                "dog_american": m["american_no"],
                "volume":    m["volume"],
                "url":       m["url"],
                "title":     m["title"],
            }

    return result


def find_value_bets(
    away_team: str,
    home_team: str,
    model_away_prob: float,
    model_home_prob: float,
    model_total: float,
    min_edge: float = 0.03,
) -> list[dict]:
    """
    Compare model probabilities vs Polymarket implied probabilities.
    Returns list of value opportunities where |model - market| >= min_edge.

    Each entry: {market_type, side, model_prob, market_prob, edge, american, url}
    """
    odds  = get_game_odds(away_team, home_team)
    edges = []

    # ── Moneyline ──────────────────────────────────────────────
    ml = odds.get("moneyline")
    if ml:
        for side, model_p, market_p, label in [
            ("away", model_away_prob, ml["away_prob"], away_team),
            ("home", model_home_prob, ml["home_prob"], home_team),
        ]:
            edge = model_p - market_p
            if edge >= min_edge:
                edges.append({
                    "market_type": "Moneyline",
                    "side":        label,
                    "model_prob":  model_p,
                    "market_prob": market_p,
                    "edge":        edge,
                    "american":    prob_to_american(market_p),
                    "url":         ml["url"],
                })

    # ── Total / O/U ────────────────────────────────────────────
    tot = odds.get("total")
    if tot and tot.get("line"):
        line = tot["line"]
        model_over_prob  = 1.0 - _poisson_prob_under(model_total, line)
        model_under_prob = _poisson_prob_under(model_total, line)

        for side, model_p, market_p, label, am in [
            ("over",  model_over_prob,  tot["over_prob"],  "Over",  tot["over_american"]),
            ("under", model_under_prob, tot["under_prob"], "Under", tot["under_american"]),
        ]:
            edge = model_p - market_p
            if edge >= min_edge:
                edges.append({
                    "market_type": f"Total ({line} Runs)",
                    "side":        label,
                    "model_prob":  model_p,
                    "market_prob": market_p,
                    "edge":        edge,
                    "american":    am,
                    "url":         tot["url"],
                })

    return edges


def _poisson_prob_under(mu: float, line: float) -> float:
    """P(X <= floor(line)) under Poisson(mu). Used for O/U edge calc."""
    from scipy.stats import poisson
    return float(poisson.cdf(int(line), mu))
