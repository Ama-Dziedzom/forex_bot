import os
import asyncio
import logging
from datetime import datetime
import pytz
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TWELVEDATA_API_KEY = os.environ["TWELVEDATA_API_KEY"]
CHAT_ID = os.environ["CHAT_ID"]

PAIRS = ["EUR/USD", "GBP/USD", "XAU/USD"]
TIMEZONE = pytz.timezone("Africa/Accra")

PAIR_EMOJI = {
    "EUR/USD": "🇪🇺",
    "GBP/USD": "🇬🇧",
    "XAU/USD": "🥇",
}

PAIR_LABEL = {
    "EUR/USD": "Euro",
    "GBP/USD": "British Pound",
    "XAU/USD": "Gold",
}

# Stop loss pip/point distance per pair
STOP_LOSS_DISTANCE = {
    "EUR/USD": 0.0015,
    "GBP/USD": 0.0020,
    "XAU/USD": 8.0,
}


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_candles(symbol, interval="1h", outputsize=60):
    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": TWELVEDATA_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "error":
            logger.error(f"API error for {symbol}: {data.get('message')}")
            return None
        values = data.get("values", [])
        return [
            {
                "close": float(v["close"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
            }
            for v in reversed(values)
        ]
    except Exception as e:
        logger.error(f"Failed to fetch {symbol}: {e}")
        return None


# ── Indicators ─────────────────────────────────────────────────────────────────

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_macd(closes):
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = macd_line * 0.85
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "hist": histogram}


def calc_ma(closes, period):
    subset = closes[-period:]
    return sum(subset) / len(subset)


def get_signal_score(rsi, macd_hist, price, ma50):
    score = 0
    # RSI — weighted more heavily
    if rsi < 25:
        score += 3
    elif rsi < 35:
        score += 2
    elif rsi < 45:
        score += 1
    elif rsi > 75:
        score -= 3
    elif rsi > 65:
        score -= 2
    elif rsi > 55:
        score -= 1
    # MACD momentum
    score += 1 if macd_hist > 0 else -1
    # Trend
    score += 1 if price > ma50 else -1
    return score


def interpret_signal(score):
    if score >= 3:
        return "BUY", "strong"
    elif score >= 1:
        return "BUY", "moderate"
    elif score <= -3:
        return "SELL", "strong"
    elif score <= -1:
        return "SELL", "moderate"
    else:
        return "WAIT", "neutral"


# ── Plain English reasons ──────────────────────────────────────────────────────

def plain_english_reason(symbol, direction, rsi, macd_hist, price, ma50):
    label = PAIR_LABEL.get(symbol, symbol)

    if direction == "BUY":
        if rsi < 25:
            return f"{label} has dropped a lot and looks very cheap right now — a bounce up is likely."
        elif rsi < 35:
            return f"{label} has been oversold and is showing early signs of recovery."
        elif macd_hist > 0 and price > ma50:
            return f"{label} momentum is picking up and the overall trend is upward."
        else:
            return f"More signals are pointing up than down for {label} right now."

    elif direction == "SELL":
        if rsi > 75:
            return f"{label} has risen too fast and looks expensive — a drop is likely."
        elif rsi > 65:
            return f"{label} is overbought and momentum is starting to turn downward."
        elif macd_hist < 0 and price < ma50:
            return f"{label} momentum is weakening and the overall trend is downward."
        else:
            return f"More signals are pointing down than up for {label} right now."

    else:
        if rsi < 40:
            return f"{label} looks cheap but momentum hasn't confirmed a bounce yet. Watch and wait."
        elif rsi > 60:
            return f"{label} looks expensive but hasn't started dropping yet. Watch and wait."
        else:
            return f"No strong direction for {label} right now. Sit this one out."


# ── Stop loss calculation ──────────────────────────────────────────────────────

def calc_stop_loss(symbol, direction, price):
    distance = STOP_LOSS_DISTANCE.get(symbol, 0.002)
    dp = 2 if "XAU" in symbol else 4
    if direction == "BUY":
        sl = price - distance
        tp = price + (distance * 2)
        return f"Stop loss: `{sl:.{dp}f}` | Take profit: `{tp:.{dp}f}`"
    elif direction == "SELL":
        sl = price + distance
        tp = price - (distance * 2)
        return f"Stop loss: `{sl:.{dp}f}` | Take profit: `{tp:.{dp}f}`"
    return ""


# ── Message formatting ─────────────────────────────────────────────────────────

def format_signal_message(symbol, price, rsi, macd_hist, ma50, score, alert=False):
    direction, strength = interpret_signal(score)
    emoji = PAIR_EMOJI.get(symbol, "📊")
    dp = 2 if "XAU" in symbol else 4
    now = datetime.now(TIMEZONE).strftime("%H:%M WAT")
    reason = plain_english_reason(symbol, direction, rsi, macd_hist, price, ma50)

    if direction == "BUY":
        action_line = f"🟢 *BUY* ({strength})"
    elif direction == "SELL":
        action_line = f"🔴 *SELL* ({strength})"
    else:
        action_line = f"⚪ *WAIT*"

    sl_line = calc_stop_loss(symbol, direction, price) if direction in ("BUY", "SELL") else ""

    msg = (
        f"{emoji} *{symbol}*\n"
        f"{action_line}\n"
        f"_{reason}_\n\n"
        f"Price: `{price:.{dp}f}`\n"
    )

    if sl_line:
        msg += f"{sl_line}\n"

    msg += f"\n⚠️ _Practice this on your Exness demo first._\n🕐 _{now}_"

    if alert:
        msg = f"🚨 *SIGNAL ALERT*\n\n" + msg

    return msg


def format_briefing(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y · %H:%M WAT")
    lines = [f"📋 *Good morning! Here's your forex briefing*\n_{now}_\n"]

    for r in results:
        sym = r["symbol"]
        emoji = PAIR_EMOJI.get(sym, "📊")
        direction, strength = interpret_signal(r["score"])
        dp = 2 if "XAU" in sym else 4
        reason = plain_english_reason(sym, direction, r["rsi"], r["macd_hist"], r["price"], r["ma50"])

        if direction == "BUY":
            action = f"🟢 BUY ({strength})"
        elif direction == "SELL":
            action = f"🔴 SELL ({strength})"
        else:
            action = "⚪ WAIT"

        lines.append(
            f"{emoji} *{sym}* — {action}\n"
            f"Price: `{r['price']:.{dp}f}`\n"
            f"_{reason}_\n"
        )

    lines.append("_Use /signal anytime to get a fresh update_")
    return "\n".join(lines)


def format_eod(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y")
    lines = [f"🌙 *End of Day — {now}*\n"]
    lines.append("Here's how the market closed today:\n")

    for r in results:
        sym = r["symbol"]
        emoji = PAIR_EMOJI.get(sym, "📊")
        direction, strength = interpret_signal(r["score"])
        dp = 2 if "XAU" in sym else 4

        if direction == "BUY":
            action = "🟢 Closed bullish"
        elif direction == "SELL":
            action = "🔴 Closed bearish"
        else:
            action = "⚪ Closed neutral"

        lines.append(f"{emoji} *{sym}* — {action} at `{r['price']:.{dp}f}`")

    lines.append("\n_Review your Exness demo trades and see how they matched these signals. That's how you get better._ 📚")
    return "\n".join(lines)


# ── Analysis ───────────────────────────────────────────────────────────────────

def analyse_pair(symbol):
    candles = fetch_candles(symbol)
    if not candles or len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    price = closes[-1]
    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    ma50 = calc_ma(closes, min(50, len(closes)))
    score = get_signal_score(rsi, macd["hist"], price, ma50)
    return {
        "symbol": symbol,
        "price": price,
        "rsi": rsi,
        "macd_hist": macd["hist"],
        "ma50": ma50,
        "score": score,
        "recent_high": max(highs[-20:]),
        "recent_low": min(lows[-20:]),
    }


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def send_morning_briefing(bot):
    logger.info("Sending morning briefing...")
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_briefing(results), parse_mode="Markdown")


async def send_eod_recap(bot):
    logger.info("Sending EOD recap...")
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_eod(results), parse_mode="Markdown")


async def check_alerts(bot):
    logger.info("Checking for signals...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        if not r:
            continue
        direction, strength = interpret_signal(r["score"])
        if direction in ("BUY", "SELL") and strength == "strong":
            msg = format_signal_message(
                r["symbol"], r["price"], r["rsi"],
                r["macd_hist"], r["ma50"], r["score"],
                alert=True
            )
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            logger.info(f"Alert sent for {pair}: {direction}")


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "trader"
    await update.message.reply_text(
        f"👋 Hey {name}! I'm *D!sForex* — your personal forex signal guide.\n\n"
        f"I watch *EUR/USD, GBP/USD and XAU/USD (Gold)* and tell you simply:\n"
        f"🟢 *BUY* — good time to buy\n"
        f"🔴 *SELL* — good time to sell\n"
        f"⚪ *WAIT* — no clear opportunity right now\n\n"
        f"Commands:\n"
        f"  /signal — check all pairs right now\n"
        f"  /briefing — morning market summary\n"
        f"  /help — how I work\n\n"
        f"_Always practice on your Exness demo before using real money!_",
        parse_mode="Markdown"
    )


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Checking the markets for you...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        if not r:
            await update.message.reply_text(f"❌ Could not fetch {pair} right now. Try again shortly.")
            continue
        msg = format_signal_message(
            r["symbol"], r["price"], r["rsi"],
            r["macd_hist"], r["ma50"], r["score"]
        )
        await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Pulling today's market picture...")
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await update.message.reply_text(format_briefing(results), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Could not fetch data right now. Try again shortly.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *How D!sForex works*\n\n"
        "I watch 3 things about each currency pair:\n\n"
        "1️⃣ *Is it cheap or expensive right now?*\n"
        "_If something has dropped a lot, it's often due a bounce back up._\n\n"
        "2️⃣ *Is the momentum going up or down?*\n"
        "_Like checking if a ball is still falling or starting to rise._\n\n"
        "3️⃣ *What is the overall trend?*\n"
        "_Is the price generally moving up or down over time?_\n\n"
        "When all 3 agree → I tell you BUY or SELL.\n"
        "When they disagree → I tell you WAIT.\n\n"
        "I also give you a suggested stop loss to limit your risk.\n\n"
        "⚠️ _Always practice on Exness demo first. Never risk money you can't afford to lose._",
        parse_mode="Markdown"
    )
    
async def send_auto_signal(bot):
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    for r in results:
        msg = format_signal_message(
            r["symbol"], r["price"], r["rsi"],
            r["macd_hist"], r["ma50"], r["score"]
        )
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    bot = app.bot

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("briefing", cmd_briefing))
    app.add_handler(CommandHandler("help", cmd_help))

    scheduler = AsyncIOScheduler(timezone=TIMEZONE)
    scheduler.add_job(send_morning_briefing, "cron", hour=7, minute=0, args=[bot])
    scheduler.add_job(send_eod_recap, "cron", hour=21, minute=0, args=[bot])
    scheduler.add_job(check_alerts, "cron", hour="6-23", minute="*/15", args=[bot])
    scheduler.add_job(send_auto_signal, "cron", hour="6-23", minute="*/15", args=[bot])
    scheduler.start()

    logger.info("D!sForex bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
