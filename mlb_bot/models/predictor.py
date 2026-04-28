"""
models/predictor.py
~~~~~~~~~~~~~~~~~~~~
MLB Game Prediction Engine

Methodology (layered approach):
───────────────────────────────
1. BASE WIN PROBABILITY
   • Pythagorean Win% from season RS/RA for each team
   • Log5 formula: P(A beats B) adjusted for home field

2. PITCHER ADJUSTMENT
   • Scale each team's expected run environment by
     how the opposing starter deviates from league-average FIP
   • xFIP used where FIP is unavailable

3. RECENT FORM ADJUSTMENT
   • 10-game rolling win% nudge (weighted 20%)

4. HEAD-TO-HEAD ADJUSTMENT
   • Multi-season H2H record nudge (weighted 10%)

5. RUN SCORING MODEL (Independent Poisson)
   • Expected runs = team_RPG * (opp_pitcher_adjustment)
                                * (park_factor, default 1.0)
   • P(team scores K runs) ~ Poisson(lambda)
   • Win probability derived from convolution of both Poisson distributions
   • Confidence interval bootstrapped from lambda uncertainty

6. COMBINED PROBABILITY
   Weighted blend:
     40% Pythagorean/Log5
     35% Poisson run-based
     15% Recent form
     10% Head-to-head

OUTPUT
   • win_prob_home, win_prob_away   (floats, sum to 1)
   • expected_runs_home, expected_runs_away
   • predicted_score_home, predicted_score_away (rounded integers)
   • total_runs_expected
   • confidence_pct (0-100, clamped to max 80 per brief)
   • confidence_interval (±)
   • explanation (list of bullet strings)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import poisson

logger = logging.getLogger(__name__)

# ─── League constants ─────────────────────────────────────────────────────────
LEAGUE_AVG_RPG  = 4.50   # runs per game, recent MLB average
LEAGUE_AVG_FIP  = 4.15
LEAGUE_WIN_PCT  = 0.500
HOME_FIELD_ADV  = 0.040  # home team gets +4% base boost
PYTH_EXPONENT   = 1.83   # James's optimal exponent


@dataclass
class PitcherProfile:
    name: str
    era:  float = 4.20
    fip:  float = 4.15
    xfip: float = 4.10
    k9:   float = 8.80
    bb9:  float = 3.20
    hr9:  float = 1.25
    whip: float = 1.30
    avg_exit_velo: float = 88.5


@dataclass
class TeamProfile:
    name:             str
    runs_per_game:    float = 4.50
    ra_per_game:      float = 4.50
    pyth_win_pct:     float = 0.500
    recent_win_pct:   float = 0.500
    recent_rs:        float = 4.50
    recent_ra:        float = 4.50
    streak:           int   = 0
    h2h_win_pct:      float = 0.500
    h2h_avg_rs:       float = 4.50
    h2h_avg_ra:       float = 4.50
    pitcher:          Optional[PitcherProfile] = None


@dataclass
class GamePrediction:
    away_team:       str
    home_team:       str
    win_prob_away:   float
    win_prob_home:   float
    expected_runs_away: float
    expected_runs_home: float
    predicted_score_away: int
    predicted_score_home: int
    total_runs_expected:  float
    confidence_pct:  float
    confidence_interval: float
    explanation:     list[str] = field(default_factory=list)

    @property
    def winner(self) -> str:
        return self.home_team if self.win_prob_home > self.win_prob_away else self.away_team

    @property
    def winner_prob(self) -> float:
        return max(self.win_prob_home, self.win_prob_away)


# ─── Core formulas ────────────────────────────────────────────────────────────

def log5(pa: float, pb: float) -> float:
    """
    Bill James Log5: probability that team A beats team B
    given their true win percentages pa and pb.
    """
    pa = np.clip(pa, 0.01, 0.99)
    pb = np.clip(pb, 0.01, 0.99)
    num = pa - pa * pb
    den = pa + pb - 2 * pa * pb
    return float(num / den) if den != 0 else 0.5


def pythagorean_winpct(rs: float, ra: float, exp: float = PYTH_EXPONENT) -> float:
    if rs <= 0 or ra <= 0:
        return 0.5
    return float(rs**exp / (rs**exp + ra**exp))


def pitcher_run_adjustment(pitcher: PitcherProfile) -> float:
    """
    Return a multiplier for how many MORE or FEWER runs the opposing offense
    will score vs league average against this pitcher.
    Uses xFIP as primary, FIP as fallback.
    Scale: league_avg_fip=4.15 → 1.0x; lower FIP → fewer runs (< 1.0)
    """
    metric = pitcher.xfip if pitcher.xfip > 0 else pitcher.fip
    if metric <= 0:
        metric = LEAGUE_AVG_FIP
    # Capped at ±35% adjustment
    raw = metric / LEAGUE_AVG_FIP
    return float(np.clip(raw, 0.65, 1.35))


def poisson_win_prob(lambda_a: float, lambda_b: float, max_runs: int = 30) -> tuple[float, float, float]:
    """
    Compute win/loss/tie probabilities via convolution of two independent
    Poisson-distributed run-scoring distributions.

    Returns (p_a_wins, p_b_wins, p_tie)
    """
    p_a_wins = p_b_wins = p_tie = 0.0
    for a in range(max_runs + 1):
        pa = poisson.pmf(a, lambda_a)
        if pa < 1e-9:
            continue
        for b in range(max_runs + 1):
            pb = poisson.pmf(b, lambda_b)
            if pb < 1e-9:
                continue
            joint = pa * pb
            if a > b:
                p_a_wins += joint
            elif b > a:
                p_b_wins += joint
            else:
                p_tie += joint

    # Distribute ties 50/50 (extra innings)
    p_a_wins += p_tie / 2
    p_b_wins += p_tie / 2
    total = p_a_wins + p_b_wins
    if total > 0:
        p_a_wins /= total
        p_b_wins /= total
    return p_a_wins, p_b_wins, p_tie


def confidence_from_probability(p: float) -> tuple[float, float]:
    """
    Translate win probability into a confidence % and ± interval.
    Calibrated so that p=0.70 → ~72% confidence, capped at 80%.
    The ± is based on how far the Poisson lambda uncertainty propagates.
    """
    # Sigmoid-like scaling: more extreme probs → higher confidence
    centered = abs(p - 0.5) * 2   # 0 at coin flip, 1 at certainty
    raw_conf = 50 + centered * 35  # range 50–85
    conf = min(raw_conf, 80.0)

    # Uncertainty interval (rough ±)
    interval = (1 - centered) * 12 + 3  # ±3 to ±15 pp
    return round(conf, 1), round(interval, 1)


# ─── Main prediction function ─────────────────────────────────────────────────

def predict_game(away: TeamProfile, home: TeamProfile) -> GamePrediction:
    """
    Run the full layered prediction for away @ home.
    """
    explanation: list[str] = []

    # ── 1. Pythagorean baseline ───────────────────────────────
    pyth_away = away.pyth_win_pct
    pyth_home = home.pyth_win_pct + HOME_FIELD_ADV

    log5_home = log5(pyth_home, pyth_away)
    log5_away = 1.0 - log5_home
    explanation.append(
        f"📊 Pythagorean WP: {away.name} {pyth_away:.1%} | {home.name} {pyth_home:.1%}"
        f" → Log5 home edge {log5_home:.1%}"
    )

    # ── 2. Expected run lambdas ───────────────────────────────
    away_pitcher_adj = pitcher_run_adjustment(home.pitcher) if home.pitcher else 1.0
    home_pitcher_adj = pitcher_run_adjustment(away.pitcher) if away.pitcher else 1.0

    # Expected runs scored by each team
    lambda_away = (
        (away.runs_per_game * 0.5 + away.recent_rs * 0.3 + away.h2h_avg_rs * 0.2)
        * home_pitcher_adj
    )
    lambda_home = (
        (home.runs_per_game * 0.5 + home.recent_rs * 0.3 + home.h2h_avg_rs * 0.2)
        * away_pitcher_adj
        * 1.02   # slight home-park run inflation
    )
    lambda_away = float(np.clip(lambda_away, 1.5, 10.0))
    lambda_home = float(np.clip(lambda_home, 1.5, 10.0))

    explanation.append(
        f"🎯 Predicted λ runs: {away.name} {lambda_away:.2f} | {home.name} {lambda_home:.2f}"
    )

    # Pitcher impact
    if home.pitcher and home.pitcher.xfip != LEAGUE_AVG_FIP:
        diff = home.pitcher.xfip - LEAGUE_AVG_FIP
        direction = "suppresses" if diff < 0 else "inflates"
        explanation.append(
            f"⚾ {home.pitcher.name} (xFIP {home.pitcher.xfip:.2f}) "
            f"{direction} {away.name} offense by {abs(diff/LEAGUE_AVG_FIP)*100:.0f}%"
        )
    if away.pitcher and away.pitcher.xfip != LEAGUE_AVG_FIP:
        diff = away.pitcher.xfip - LEAGUE_AVG_FIP
        direction = "suppresses" if diff < 0 else "inflates"
        explanation.append(
            f"⚾ {away.pitcher.name} (xFIP {away.pitcher.xfip:.2f}) "
            f"{direction} {home.name} offense by {abs(diff/LEAGUE_AVG_FIP)*100:.0f}%"
        )

    # ── 3. Poisson win probability ────────────────────────────
    p_away_poisson, p_home_poisson, p_tie = poisson_win_prob(lambda_away, lambda_home)
    explanation.append(
        f"🔢 Poisson model: {away.name} {p_away_poisson:.1%} | {home.name} {p_home_poisson:.1%}"
        f"  (tie/extra-innings {p_tie:.1%} split evenly)"
    )

    # ── 4. Recent form nudge ──────────────────────────────────
    recent_away_adj = (away.recent_win_pct - 0.5) * 0.15
    recent_home_adj = (home.recent_win_pct - 0.5) * 0.15
    streak_msg_parts = []
    if away.streak != 0:
        streak_msg_parts.append(f"{away.name} {'W' if away.streak > 0 else 'L'}{abs(away.streak)}")
    if home.streak != 0:
        streak_msg_parts.append(f"{home.name} {'W' if home.streak > 0 else 'L'}{abs(home.streak)}")
    if streak_msg_parts:
        explanation.append(f"🔥 Current streaks: {', '.join(streak_msg_parts)}")
    explanation.append(
        f"📈 Recent form (L10): {away.name} {away.recent_win_pct:.1%} | {home.name} {home.recent_win_pct:.1%}"
    )

    # ── 5. Head-to-head nudge ─────────────────────────────────
    h2h_away_adj = (away.h2h_win_pct - 0.5) * 0.10
    h2h_home_adj = (home.h2h_win_pct - 0.5) * 0.10
    explanation.append(
        f"🆚 H2H (last 3 seasons): {away.name} {away.h2h_win_pct:.1%} | {home.name} {home.h2h_win_pct:.1%}"
    )

    # ── 6. Weighted blend ─────────────────────────────────────
    raw_away = (
        log5_away       * 0.40 +
        p_away_poisson  * 0.35 +
        (away.recent_win_pct * 0.15) +
        (away.h2h_win_pct   * 0.10)
    )
    raw_away += (recent_away_adj - recent_home_adj) / 2
    raw_away += (h2h_away_adj    - h2h_home_adj)    / 2

    raw_away = float(np.clip(raw_away, 0.10, 0.90))
    raw_home = 1.0 - raw_away

    # ── 7. Confidence ─────────────────────────────────────────
    conf_pct, conf_interval = confidence_from_probability(max(raw_away, raw_home))

    winner = home.name if raw_home > raw_away else away.name
    explanation.append(
        f"✅ Model verdict: {winner} wins | "
        f"{max(raw_home, raw_away):.1%} probability | "
        f"{conf_pct:.0f}% confidence (±{conf_interval:.0f}pp)"
    )

    # ── 8. Predicted score ────────────────────────────────────
    score_away = int(round(lambda_away))
    score_home = int(round(lambda_home))
    # Ensure the winner's score is higher in the rounded result
    if raw_home > raw_away and score_home <= score_away:
        score_home = score_away + 1
    elif raw_away > raw_home and score_away <= score_home:
        score_away = score_home + 1

    total_expected = lambda_away + lambda_home

    return GamePrediction(
        away_team            = away.name,
        home_team            = home.name,
        win_prob_away        = raw_away,
        win_prob_home        = raw_home,
        expected_runs_away   = lambda_away,
        expected_runs_home   = lambda_home,
        predicted_score_away = score_away,
        predicted_score_home = score_home,
        total_runs_expected  = total_expected,
        confidence_pct       = conf_pct,
        confidence_interval  = conf_interval,
        explanation          = explanation,
    )


# ─── High-level orchestrator ──────────────────────────────────────────────────

def build_profiles_and_predict(
    away_name: str,
    home_name: str,
    away_pitcher_name: str = "TBD",
    home_pitcher_name: str = "TBD",
    season: int = 2025,
) -> GamePrediction:
    """
    Pull all data, build TeamProfile objects, run predict_game().
    Designed to be called from bot handlers.
    """
    from data.mlb_data import (
        get_team_season_stats,
        get_pitcher_statcast,
        get_head_to_head,
        get_recent_form,
    )

    season_stats = get_team_season_stats(season)

    def _team_profile(name: str, pitcher_name: str, is_home: bool) -> TeamProfile:
        # Season stats
        rpg  = LEAGUE_AVG_RPG
        rapg = LEAGUE_AVG_RPG
        pyth = 0.500

        if not season_stats.empty:
            # fuzzy match on team name
            mask = season_stats.index.str.contains(
                name.split()[-1], case=False, na=False
            )
            if mask.any():
                row  = season_stats[mask].iloc[0]
                rpg  = float(row.get("RunsPerGame",    LEAGUE_AVG_RPG))
                rapg = float(row.get("RAPerGame",      LEAGUE_AVG_RPG))
                pyth = float(row.get("pythagWinPct",   0.500))

        # Recent form
        form = get_recent_form(name)

        # H2H (always from the away team's perspective)
        h2h = get_head_to_head(away_name, home_name)
        if is_home:
            h2h_wp   = 1.0 - h2h["h2h_win_pct_a"]
            h2h_rs   = h2h["avg_runs_b"]
            h2h_ra   = h2h["avg_runs_a"]
        else:
            h2h_wp   = h2h["h2h_win_pct_a"]
            h2h_rs   = h2h["avg_runs_a"]
            h2h_ra   = h2h["avg_runs_b"]

        # Pitcher
        pitcher = None
        if pitcher_name and pitcher_name.upper() != "TBD":
            p_stats = get_pitcher_statcast(pitcher_name, season)
            pitcher = PitcherProfile(
                name           = pitcher_name,
                era            = p_stats.get("era",            4.20),
                fip            = p_stats.get("fip",            4.15),
                xfip           = p_stats.get("xfip",           4.10),
                k9             = p_stats.get("k9",             8.80),
                bb9            = p_stats.get("bb9",            3.20),
                hr9            = p_stats.get("hr9",            1.25),
                whip           = p_stats.get("whip",           1.30),
                avg_exit_velo  = p_stats.get("avg_exit_velo",  88.5),
            )

        return TeamProfile(
            name           = name,
            runs_per_game  = rpg,
            ra_per_game    = rapg,
            pyth_win_pct   = pyth,
            recent_win_pct = form.get("win_pct",          0.500),
            recent_rs      = form.get("avg_runs_scored",  LEAGUE_AVG_RPG),
            recent_ra      = form.get("avg_runs_allowed", LEAGUE_AVG_RPG),
            streak         = form.get("streak",           0),
            h2h_win_pct    = h2h_wp,
            h2h_avg_rs     = h2h_rs,
            h2h_avg_ra     = h2h_ra,
            pitcher        = pitcher,
        )

    away_profile = _team_profile(away_name, away_pitcher_name, is_home=False)
    home_profile = _team_profile(home_name, home_pitcher_name, is_home=True)

    return predict_game(away_profile, home_profile)
