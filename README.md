# D!sForex — Railway Deployment

Forex signal guide bot for Telegram. Watches EUR/USD, GBP/USD, XAU/USD
and sends alerts every 15 minutes during market hours.

## Files
- `bot.py` — main bot (NO credentials inside — safe to push to GitHub)
- `requirements.txt` — dependencies
- `Procfile` — tells Railway how to run the bot
- `.gitignore` — prevents secrets from being pushed to GitHub

## Deploy to Railway

### Step 1 — Push to GitHub
Make sure these files are in your repo.
Your bot.py has NO hardcoded credentials — safe to push.

### Step 2 — Create Railway project
1. Go to https://railway.app
2. Sign up with GitHub (free)
3. Click **New Project → Deploy from GitHub repo**
4. Select your forex_bot repo
5. Railway auto-detects the Procfile

### Step 3 — Add environment variables
In Railway dashboard → your project → **Variables** tab, add:

| Variable | Value |
|---|---|
| TELEGRAM_TOKEN | your token from @BotFather |
| TWELVEDATA_API_KEY | your key from twelvedata.com |
| CHAT_ID | your Telegram chat ID |

### Step 4 — Deploy
Click Deploy. Railway will install requirements and start the bot.
Check the logs — you should see:
```
D!sForex bot is running on Railway...
```

## What the bot does

| Time | Action |
|---|---|
| 7:00 WAT daily | Morning briefing sent |
| Every 15 mins (6am-10pm) | Checks for strong signals |
| 9:00 WAT daily | End of day recap |

## Commands
- `/start` — introduction
- `/signal` — check all pairs now
- `/briefing` — today's briefing on demand
- `/help` — how indicators work

## Security rules
- NEVER hardcode credentials in bot.py
- NEVER commit .env files to GitHub
- All secrets go in Railway environment variables ONLY
- If token is exposed → revoke immediately via @BotFather
