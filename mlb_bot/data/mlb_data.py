"""
data/mlb_data.py
~~~~~~~~~~~~~~~~
Fetches:
  • Today's schedule (MLB Stats API)
  • Team seasonal batting / pitching logs (pybaseball / Baseball Savant)
  • Pitcher-level Statcast data (spin rate, exit velo allowed, xFIP, etc.)
  • Head-to-head historical results
  • Optional: BigQuery public baseball dataset for deeper history

All heavy calls are lightly cached in-process (dict keyed by date/season)
to avoid hammering the APIs during a single bot session.
"""

from __future__ import annotations

import datetime
import functools
import logging
from typing import Optional

import numpy as np
import pandas as pd
import requests
import statsapi  # pip install mlb-statsapi

logger = logging.getLogger(__name__)

# ─── in-process cache ────────────────────────────────────────────────────────
_cache: dict = {}


def _cached(key: str):
    """Simple memoize decorator for expensive calls."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            cache_key = f"{key}:{args}:{kwargs}"
            if cache_key not in _cache:
                _cache[cache_key] = fn(*args, **kwargs)
            return _cache[cache_key]
        return wrapper
    return decorator


# ─── Schedule ────────────────────────────────────────────────────────────────

def get_todays_games(date: Optional[datetime.date] = None) -> list[dict]:
    """
    Return a list of today's MLB games via the official Stats API.
    Each entry has:
        game_pk, away_team, home_team, game_time,
        away_probable_pitcher, home_probable_pitcher,
        venue, status
    """
    target = date or datetime.date.today()
    date_str = target.strftime("%Y-%m-%d")
    try:
        sched = statsapi.schedule(date=date_str)
    except Exception as exc:
        logger.error("statsapi schedule error: %s", exc)
        return []

    games = []
    for g in sched:
        games.append({
            "game_pk":               g.get("game_id"),
            "away_team":             g.get("away_name", ""),
            "home_team":             g.get("home_name", ""),
            "game_time":             g.get("game_datetime", ""),
            "away_probable_pitcher": g.get("away_probable_pitcher", "TBD"),
            "home_probable_pitcher": g.get("home_probable_pitcher", "TBD"),
            "venue":                 g.get("venue_name", ""),
            "status":                g.get("status", ""),
            "away_score":            g.get("away_score"),
            "home_score":            g.get("home_score"),
        })
    return games


# ─── Team season stats ───────────────────────────────────────────────────────

@_cached("team_season_stats")
def get_team_season_stats(season: int) -> pd.DataFrame:
    """
    Pull team-level batting + pitching via pybaseball.
    Returns DataFrame indexed by team abbreviation with columns:
        R, RA, W, L, winPct, RunsPerGame, RAPerGame, pythagWinPct
    Falls back to a hardcoded league-average row if pybaseball fails.
    """
    try:
        import pybaseball as pb
        pb.cache.enable()

        batting  = pb.team_batting(season)
        pitching = pb.team_pitching(season)

        # pybaseball column names vary by season; normalise
        bat_cols = {c: c.strip() for c in batting.columns}
        pit_cols = {c: c.strip() for c in pitching.columns}
        batting.rename(columns=bat_cols, inplace=True)
        pitching.rename(columns=pit_cols, inplace=True)

        df = batting[["Team", "R", "G"]].copy()
        df.rename(columns={"R": "RS", "G": "G_bat"}, inplace=True)

        # runs allowed from pitching log
        if "R" in pitching.columns:
            pit_sub = pitching[["Team", "R", "G"]].copy()
            pit_sub.rename(columns={"R": "RA", "G": "G_pit"}, inplace=True)
            df = df.merge(pit_sub, on="Team", how="left")
        else:
            df["RA"] = np.nan
            df["G_pit"] = df["G_bat"]

        df["RunsPerGame"] = df["RS"] / df["G_bat"]
        df["RAPerGame"]   = df["RA"] / df["G_bat"]

        # Pythagorean win expectation (exp=1.83 is the sabermetric sweet spot)
        exp = 1.83
        df["pythagWinPct"] = df["RS"]**exp / (df["RS"]**exp + df["RA"]**exp)

        df.set_index("Team", inplace=True)
        return df

    except Exception as exc:
        logger.warning("pybaseball team stats failed (%s) – using estimates", exc)
        return pd.DataFrame()


# ─── Pitcher Statcast ─────────────────────────────────────────────────────────

@_cached("pitcher_statcast")
def get_pitcher_statcast(pitcher_name: str, season: int) -> dict:
    """
    Pull Statcast-level metrics for a starting pitcher.
    Returns dict with: era, fip, xfip, k9, bb9, hr9, avg_exit_velo, whip
    Falls back to league-average values if not found.
    """
    league_avg = {
        "era": 4.20, "fip": 4.15, "xfip": 4.10,
        "k9": 8.8,   "bb9": 3.2,  "hr9": 1.25,
        "avg_exit_velo": 88.5, "whip": 1.30,
        "name": pitcher_name,
    }
    try:
        import pybaseball as pb
        pb.cache.enable()

        # Search for pitcher id
        player = pb.playerid_lookup(
            pitcher_name.split()[-1],
            pitcher_name.split()[0] if len(pitcher_name.split()) > 1 else ""
        )
        if player.empty:
            return league_avg

        pid = int(player.iloc[0]["key_mlbam"])
        start = f"{season}-03-01"
        end   = f"{season}-11-01"

        sc = pb.statcast_pitcher(start, end, player_id=pid)
        if sc.empty:
            return league_avg

        stats = {
            "name":            pitcher_name,
            "avg_exit_velo":   float(sc["launch_speed"].dropna().mean()) if "launch_speed" in sc else 88.5,
            "era":             league_avg["era"],   # calculated below
            "fip":             league_avg["fip"],
            "xfip":            league_avg["xfip"],
            "k9":              league_avg["k9"],
            "bb9":             league_avg["bb9"],
            "hr9":             league_avg["hr9"],
            "whip":            league_avg["whip"],
        }

        # Pull FanGraphs-style ERA / FIP from pybaseball pitching stats
        try:
            fg = pb.pitching_stats(season, season, qual=0)
            row = fg[fg["Name"].str.contains(pitcher_name.split()[-1], na=False, case=False)]
            if not row.empty:
                r = row.iloc[0]
                stats["era"]  = float(r.get("ERA",  league_avg["era"]))
                stats["fip"]  = float(r.get("FIP",  league_avg["fip"]))
                stats["xfip"] = float(r.get("xFIP", league_avg["xfip"]))
                stats["k9"]   = float(r.get("K/9",  league_avg["k9"]))
                stats["bb9"]  = float(r.get("BB/9", league_avg["bb9"]))
                stats["whip"] = float(r.get("WHIP", league_avg["whip"]))
        except Exception:
            pass

        return stats

    except Exception as exc:
        logger.warning("Pitcher Statcast fetch failed for %s: %s", pitcher_name, exc)
        return league_avg


# ─── Head-to-head history ────────────────────────────────────────────────────

def get_head_to_head(team_a: str, team_b: str, seasons: int = 3) -> dict:
    """
    Pull head-to-head results for team_a vs team_b over the last N seasons.
    Returns: {wins_a, wins_b, avg_runs_a, avg_runs_b, h2h_win_pct_a}
    Uses statsapi game logs.
    """
    current_year = datetime.date.today().year
    wins_a = wins_b = 0
    runs_a_list: list[float] = []
    runs_b_list: list[float] = []

    for year in range(current_year - seasons, current_year):
        try:
            schedule = statsapi.schedule(
                start_date=f"{year}-04-01",
                end_date=f"{year}-10-01",
            )
            for g in schedule:
                if g.get("status") != "Final":
                    continue
                at  = g.get("away_name", "")
                ht  = g.get("home_name", "")
                asc = g.get("away_score", 0) or 0
                hsc = g.get("home_score", 0) or 0

                # Loose team name match
                a_in_game = (team_a.lower() in at.lower() or team_a.lower() in ht.lower())
                b_in_game = (team_b.lower() in at.lower() or team_b.lower() in ht.lower())
                if not (a_in_game and b_in_game):
                    continue

                # Determine which team is A in this game
                a_is_away = team_a.lower() in at.lower()
                score_a   = asc if a_is_away else hsc
                score_b   = hsc if a_is_away else asc

                runs_a_list.append(score_a)
                runs_b_list.append(score_b)
                if score_a > score_b:
                    wins_a += 1
                else:
                    wins_b += 1

        except Exception as exc:
            logger.warning("H2H fetch error year %s: %s", year, exc)

    total = wins_a + wins_b
    return {
        "wins_a":       wins_a,
        "wins_b":       wins_b,
        "total_games":  total,
        "avg_runs_a":   float(np.mean(runs_a_list)) if runs_a_list else 4.5,
        "avg_runs_b":   float(np.mean(runs_b_list)) if runs_b_list else 4.5,
        "h2h_win_pct_a": (wins_a / total) if total > 0 else 0.5,
    }


# ─── Recent form ─────────────────────────────────────────────────────────────

def get_recent_form(team_name: str, last_n: int = 10) -> dict:
    """
    Fetch the last N completed games for a team.
    Returns: {record, win_pct, avg_runs_scored, avg_runs_allowed, streak}
    """
    today     = datetime.date.today()
    start     = today - datetime.timedelta(days=30)
    try:
        schedule = statsapi.schedule(
            start_date=start.strftime("%Y-%m-%d"),
            end_date=today.strftime("%Y-%m-%d"),
        )
    except Exception as exc:
        logger.warning("Recent form fetch failed: %s", exc)
        return {"win_pct": 0.5, "avg_runs_scored": 4.5, "avg_runs_allowed": 4.5, "streak": 0}

    results = []
    for g in schedule:
        if g.get("status") != "Final":
            continue
        at  = g.get("away_name", "")
        ht  = g.get("home_name", "")
        if team_name.lower() not in at.lower() and team_name.lower() not in ht.lower():
            continue
        is_away = team_name.lower() in at.lower()
        rs = (g.get("away_score") or 0) if is_away else (g.get("home_score") or 0)
        ra = (g.get("home_score") or 0) if is_away else (g.get("away_score") or 0)
        results.append({"rs": rs, "ra": ra, "win": rs > ra})

    results = results[-last_n:]
    if not results:
        return {"win_pct": 0.5, "avg_runs_scored": 4.5, "avg_runs_allowed": 4.5, "streak": 0}

    wins = sum(r["win"] for r in results)
    # Current streak
    streak = 0
    last_win = results[-1]["win"]
    for r in reversed(results):
        if r["win"] == last_win:
            streak += 1 if last_win else -1
        else:
            break

    return {
        "win_pct":          wins / len(results),
        "avg_runs_scored":  float(np.mean([r["rs"] for r in results])),
        "avg_runs_allowed": float(np.mean([r["ra"] for r in results])),
        "streak":           streak,
        "last_n":           len(results),
    }


# ─── BigQuery (optional advanced pull) ───────────────────────────────────────

def get_bigquery_team_stats(project_id: str, team_name: str, season: int) -> dict:
    """
    Optional: Pull from Google Cloud's public baseball BigQuery dataset.
    Dataset: bigquery-public-data.baseball
    Requires GOOGLE_APPLICATION_CREDENTIALS env var to be set.
    """
    try:
        from google.cloud import bigquery
        client = bigquery.Client(project=project_id)

        query = f"""
        SELECT
            homeTeamName,
            awayTeamName,
            homeScore,
            awayScore,
            startTime
        FROM `bigquery-public-data.baseball.games_wide`
        WHERE EXTRACT(YEAR FROM startTime) = {season}
          AND (homeTeamName LIKE '%{team_name}%'
               OR awayTeamName LIKE '%{team_name}%')
          AND status = 'closed'
        ORDER BY startTime DESC
        LIMIT 200
        """
        df = client.query(query).to_dataframe()
        if df.empty:
            return {}

        # Compute stats from the BigQuery result
        is_home = df["homeTeamName"].str.contains(team_name, case=False, na=False)
        rs = np.where(is_home, df["homeScore"], df["awayScore"])
        ra = np.where(is_home, df["awayScore"], df["homeScore"])

        return {
            "games":            len(df),
            "avg_runs_scored":  float(np.nanmean(rs)),
            "avg_runs_allowed": float(np.nanmean(ra)),
            "win_pct":          float(np.mean(rs > ra)),
        }

    except ImportError:
        logger.warning("google-cloud-bigquery not installed; skipping BQ pull")
        return {}
    except Exception as exc:
        logger.warning("BigQuery fetch error: %s", exc)
        return {}
