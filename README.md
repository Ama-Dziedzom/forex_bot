# Forex Signal Guide Bot — Setup Instructions

A Telegram bot that watches EUR/USD, GBP/USD, and XAU/USD and sends
you signal alerts based on RSI, MACD, and MA50 indicators.

---

## Step 1 — Get your Telegram Bot Token (2 mins)

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Give it a name e.g. `Ama Forex Guide`
4. Give it a username e.g. `ama_forex_bot`
5. BotFather will give you a token like:
   `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxx`
6. Save that — it's your `TELEGRAM_TOKEN`

---

## Step 2 — Get your Chat ID (2 mins)

1. Start your new bot by searching for it in Telegram and clicking Start
2. Go to this URL in your browser (replace YOUR_TOKEN):
   `https://api.telegram.org/botYOUR_TOKEN/getUpdates`
3. Send any message to your bot first, then refresh the URL
4. Find `"chat":{"id":XXXXXXXXX}` — that number is your `CHAT_ID`

---

## Step 3 — Get your Twelve Data API key (2 mins)

1. Go to https://twelvedata.com
2. Sign up for a free account
3. Go to your dashboard → API Keys
4. Copy your key — it's your `TWELVEDATA_API_KEY`

Free tier gives you 800 API calls/day — more than enough.

---

## Step 4 — Deploy on Render (free, 5 mins)

1. Push your code to a GitHub repo (bot.py, requirements.txt, render.yaml)
2. Go to https://render.com and sign up free
3. Click **New → Blueprint** and connect your GitHub repo
4. Render will detect render.yaml automatically
5. Add your 3 environment variables:
   - `TELEGRAM_TOKEN`
   - `TWELVEDATA_API_KEY`
   - `CHAT_ID`
6. Click Deploy — your bot is live!

---

## What the bot sends you

### Morning briefing (7am WAT daily)
```
📋 Daily Forex Briefing
Monday, 21 Apr 2026 · 07:00 WAT

🇪🇺 EUR/USD
  Price: 1.0845 | RSI: 38.2
  🟢 Leaning BUY (moderate)

🇬🇧 GBP/USD
  Price: 1.2640 | RSI: 61.5
  ⚪ Neutral — wait

🥇 XAU/USD
  Price: 2341.20 | RSI: 72.1
  🔴 Leaning SELL (moderate)
```

### Strong signal alert (when it fires)
```
🥇 XAU/USD — 🔴 STRONG SELL SIGNAL
Price: 2341.20

Why:
  • RSI 74.3 — overbought, expect pullback
  • MACD momentum is bearish ↓
  • Price is below MA50 (2338.10)

⚠️ Study this signal, not financial advice.
```

### EOD recap (9pm WAT daily)
Summary of how each pair ended the day.

---

## Bot Commands

| Command | What it does |
|---|---|
| `/start` | Introduction |
| `/signal` | Check all 3 pairs right now |
| `/signal XAUUSD` | Check one pair |
| `/briefing` | Get today's briefing on demand |
| `/help` | How the bot works |

---

## How signals work

A **strong signal** fires only when all 3 indicators agree:

| Indicator | Buy condition | Sell condition |
|---|---|---|
| RSI | Below 35 | Above 65 |
| MACD histogram | Positive | Negative |
| MA50 trend | Price above MA | Price below MA |

Score of +3 = strong buy alert sent
Score of -3 = strong sell alert sent
Mixed = no alert (wait for confluence)

---

## Disclaimer
This bot is for educational purposes. It helps you study market signals
and understand technical analysis. It does not execute trades. Always
practice on a demo account (like your Exness demo) before trading real money.
