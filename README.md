# ⚾ MLB Prediction Telegram Bot

A powerful MLB prediction bot for Telegram that combines **Google Cloud Statcast data**, a multi-layer statistical model, and live **Polymarket odds** to give you game predictions with win probability, projected scores, and value-edge alerts.

---

## 🚀 Quick Start

### 1. Install Python Dependencies

```bash
pip install -r requirements.txt
```

> Requires **Python 3.10+**

### 2. Configure Your Environment

Your `.env` file already has your Telegram token set. The bot works out-of-the-box with pybaseball (no API key needed). Google Cloud BigQuery is optional for deeper historical data.

```bash
# Optional: copy the example and review settings
cp .env.example .env
```

### 3. Run the Bot

```bash
python main.py
```

That's it! Open Telegram and message your bot.

---

## 📱 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message |
| `/today` | List all today's MLB games with probable starters |
| `/analyze` | Interactive game picker — tap a button to analyze |
| `/analyze_1` | Directly analyze game #1 from today's list |
| `/analyze_N` | Directly analyze game #N (up to 16) |
| `/odds` | Show live Polymarket odds for all today's games |
| `/help` | Full command reference |

---

## 🧠 How the Prediction Model Works

The bot uses a **6-layer weighted model**:

### Layer 1 — Pythagorean Win% (season baseline)
Uses the James Pythagorean formula with exponent 1.83:
```
Win% = RS^1.83 / (RS^1.83 + RA^1.83)
```
Pulled from full-season batting and pitching logs via **pybaseball**.

### Layer 2 — Log5 Formula (true matchup probability)
Given two teams' true win percentages, Log5 gives the actual probability one beats the other, eliminating opponent-strength double-counting:
```
P(A beats B) = (Pa - Pa×Pb) / (Pa + Pb - 2×Pa×Pb)
```
Home field advantage: **+4%** applied to the home team baseline.

### Layer 3 — Pitcher Statcast Adjustment
Pulls **xFIP** (and FIP fallback) for each probable starter from Baseball Savant via pybaseball. The opposing offense's expected runs are scaled by how the pitcher deviates from league-average xFIP (4.15).

Also uses: **exit velocity allowed, K/9, BB/9, WHIP**.

### Layer 4 — Independent Poisson Run Scoring
Each team's expected runs (λ) are modeled as Poisson-distributed. Win probability is computed by convolving both distributions across all run-score combinations.

λ_team = (50% season RPG + 30% recent RS + 20% H2H RS) × pitcher_adjustment

### Layer 5 — Recent Form (L10)
10-game rolling win%, average runs scored/allowed, and current win/loss streak. Weighted at **15%**.

### Layer 6 — Head-to-Head History
3 seasons of H2H results pulled from the MLB Stats API. Win%, average scores by side. Weighted at **10%**.

### Final Blend
```
Combined = 40% Log5/Pyth + 35% Poisson + 15% Recent Form + 10% H2H
```
**Confidence is capped at 80%** as specified.

---

## 💰 Polymarket Integration

The bot queries the [Polymarket Gamma API](https://gamma-api.polymarket.com) (no key required) for:

- **Moneyline** — who wins the game
- **Total Runs (O/U)** — over/under run markets
- **Run Line** — spread markets (±1.5)

### Value Edge Detection

The bot compares its model probability against market implied probability. If the edge is ≥ **3%** (configurable via `MIN_EDGE_PCT`), it fires a 🚨 **VALUE EDGE ALERT** with:

- Market type and side (e.g., "Moneyline — Yankees")
- Market implied probability vs model probability
- Edge percentage
- American odds
- Direct link to the Polymarket market

---

## 🌩️ Google Cloud BigQuery (Optional)

For deeper historical data, the bot can query the public `bigquery-public-data.baseball` dataset.

1. Create a GCP project and enable the BigQuery API
2. Create a service account with BigQuery read access
3. Download the JSON key file
4. Set in `.env`:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
   GCP_PROJECT_ID=your-project-id
   ```

Without BigQuery, the bot uses pybaseball + the MLB Stats API, which covers everything needed.

---

## 🗂️ Project Structure

```
mlb_bot/
├── main.py                  # Bot entry point
├── config.py                # Environment config
├── requirements.txt
├── .env                     # Your credentials (pre-filled with token)
├── .env.example             # Template
├── data/
│   ├── mlb_data.py          # Statcast, schedule, team/pitcher data
│   └── polymarket.py        # Polymarket API + value-edge calc
├── models/
│   └── predictor.py         # Full prediction engine
├── handlers/
│   └── bot_handlers.py      # Telegram command handlers
└── utils/
    └── formatter.py         # MarkdownV2 message formatting
```

---

## ⚙️ Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | _(set)_ | Your bot token from @BotFather |
| `GCP_PROJECT_ID` | _(blank)_ | GCP project for BigQuery (optional) |
| `GOOGLE_APPLICATION_CREDENTIALS` | _(blank)_ | Path to GCP service account JSON |
| `VALUE_EDGE_THRESHOLD` | `0.05` | Min model-market edge to alert (5%) |
| `MIN_EDGE_PCT` | `3` | Min edge % shown in alerts |

---

## 📦 Dependencies

| Package | Purpose |
|---------|---------|
| `python-telegram-bot` | Telegram Bot API async wrapper |
| `pybaseball` | Baseball Savant / FanGraphs Statcast data |
| `mlb-statsapi` | Official MLB Stats API schedule & results |
| `scipy` | Poisson distribution math |
| `scikit-learn` | Supporting ML utilities |
| `google-cloud-bigquery` | Optional BigQuery historical data |
| `requests` / `httpx` | Polymarket API calls |

---

## ⚠️ Disclaimer

This bot is **for entertainment and educational purposes only**. It is not financial advice. Sports betting involves risk. Past model performance does not guarantee future results. Please gamble responsibly.

---

## 🛠️ Troubleshooting

**Bot doesn't respond:** Check that `TELEGRAM_BOT_TOKEN` is set correctly in `.env`.

**Slow analysis:** First run pulls Statcast data for the season — subsequent calls use in-process cache. Expect 15–30s on first query.

**No Polymarket markets found:** Markets may not be listed until closer to game time, or may not exist for all games. The prediction still runs normally.

**pybaseball quota error:** Baseball Savant occasionally rate-limits. The bot will fall back to league-average stats for that call and continue.
