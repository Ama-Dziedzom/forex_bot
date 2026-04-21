import os
import asyncio
import logging
from datetime import datetime
import pytz
import requests
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ── PASTE YOUR CREDENTIALS DIRECTLY HERE ──────────────────────────────────────
TELEGRAM_TOKEN = "8716627952:AAEJbaMTA7oi8OC6CXxduZK5IAbXbAMTRAw"
TWELVEDATA_API_KEY = "8b6776bf8717425da1a199f096baf6ec"
CHAT_ID = "6083157713"
# ──────────────────────────────────────────────────────────────────────────────

PAIRS = ["EUR/USD", "GBP/USD", "XAU/USD"]
TIMEZONE = pytz.timezone("Africa/Accra")

PAIR_EMOJI = {
    "EUR/USD": "🇪🇺",
    "GBP/USD": "🇬🇧",
    "XAU/USD": "🥇",
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
        return [{"close": float(v["close"]), "high": float(v["high"]), "low": float(v["low"])} for v in reversed(values)]
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
    if rsi < 30:
        score += 2
    elif rsi < 45:
        score += 1
    elif rsi > 70:
        score -= 2
    elif rsi > 55:
        score -= 1
    score += 1 if macd_hist > 0 else -1
    score += 1 if price > ma50 else -1
    return score


def interpret_signal(score):
    if score >= 3:
        return "BUY", "strong"
    elif score == 2:
        return "BUY", "moderate"
    elif score <= -3:
        return "SELL", "strong"
    elif score == -2:
        return "SELL", "moderate"
    else:
        return "NEUTRAL", "weak"


# ── Formatting ─────────────────────────────────────────────────────────────────

def format_signal_message(symbol, price, rsi, macd_hist, ma50, score):
    direction, strength = interpret_signal(score)
    emoji = PAIR_EMOJI.get(symbol, "📊")
    if direction == "BUY":
        signal_line = f"🟢 *{strength.upper()} BUY SIGNAL*"
    elif direction == "SELL":
        signal_line = f"🔴 *{strength.upper()} SELL SIGNAL*"
    else:
        return ""
    reasons = []
    if rsi < 30:
        reasons.append(f"RSI {rsi} — deeply oversold")
    elif rsi < 45:
        reasons.append(f"RSI {rsi} — oversold territory")
    elif rsi > 70:
        reasons.append(f"RSI {rsi} — overbought, expect pullback")
    elif rsi > 55:
        reasons.append(f"RSI {rsi} — overbought pressure")
    reasons.append("MACD momentum is " + ("bullish ↑" if macd_hist > 0 else "bearish ↓"))
    reasons.append("Price is " + ("above" if price > ma50 else "below") + f" MA50")
    reason_text = "\n".join(f"  • {r}" for r in reasons)
    dp = 2 if "XAU" in symbol else 4
    now = datetime.now(TIMEZONE).strftime("%H:%M WAT")
    return (
        f"{emoji} *{symbol}* — {signal_line}\n"
        f"Price: `{price:.{dp}f}`\n\n"
        f"Why:\n{reason_text}\n\n"
        f"⚠️ _Study this signal — not financial advice._\n"
        f"🕐 _{now}_"
    )


def format_briefing(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y · %H:%M WAT")
    lines = [f"📋 *Daily Forex Briefing*\n_{now}_\n"]
    for r in results:
        sym = r["symbol"]
        emoji = PAIR_EMOJI.get(sym, "📊")
        direction, strength = interpret_signal(r["score"])
        dp = 2 if "XAU" in sym else 4
        if direction == "BUY":
            mood = f"🟢 Leaning BUY ({strength})"
        elif direction == "SELL":
            mood = f"🔴 Leaning SELL ({strength})"
        else:
            mood = "⚪ Neutral — wait"
        lines.append(f"{emoji} *{sym}*\n  Price: `{r['price']:.{dp}f}` | RSI: `{r['rsi']}`\n  {mood}\n")
    lines.append("_Use /signal to check any pair now_")
    return "\n".join(lines)


def format_eod(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y")
    lines = [f"🌙 *End of Day Recap — {now}*\n"]
    buys = [r for r in results if interpret_signal(r["score"])[0] == "BUY"]
    sells = [r for r in results if interpret_signal(r["score"])[0] == "SELL"]
    neutrals = [r for r in results if interpret_signal(r["score"])[0] == "NEUTRAL"]
    if buys:
        lines.append("🟢 *Bullish:* " + ", ".join(r["symbol"] for r in buys))
    if sells:
        lines.append("🔴 *Bearish:* " + ", ".join(r["symbol"] for r in sells))
    if neutrals:
        lines.append("⚪ *Neutral:* " + ", ".join(r["symbol"] for r in neutrals))
    lines.append("\n_Compare these against your Exness demo trades today. That's how you learn._ 📚")
    return "\n".join(lines)


# ── Analysis ───────────────────────────────────────────────────────────────────

def analyse_pair(symbol):
    candles = fetch_candles(symbol)
    if not candles or len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    price = closes[-1]
    rsi = calc_rsi(closes)
    macd = calc_macd(closes)
    ma50 = calc_ma(closes, min(50, len(closes)))
    score = get_signal_score(rsi, macd["hist"], price, ma50)
    return {"symbol": symbol, "price": price, "rsi": rsi, "macd_hist": macd["hist"], "ma50": ma50, "score": score}


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def send_morning_briefing(bot):
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_briefing(results), parse_mode="Markdown")


async def send_eod_recap(bot):
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_eod(results), parse_mode="Markdown")


async def check_alerts(bot):
    for pair in PAIRS:
        r = analyse_pair(pair)
        if not r:
            continue
        direction, strength = interpret_signal(r["score"])
        if strength == "strong" and direction != "NEUTRAL":
            msg = format_signal_message(r["symbol"], r["price"], r["rsi"], r["macd_hist"], r["ma50"], r["score"])
            if msg:
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_start(update, context):
    name = update.effective_user.first_name or "trader"
    await update.message.reply_text(
        f"👋 Hey {name}! I'm *D!sForex* — your personal forex signal guide.\n\n"
        f"I watch *EUR/USD, GBP/USD, XAU/USD* and alert you when signals look strong.\n\n"
        f"Commands:\n"
        f"  /signal — check all pairs now\n"
        f"  /briefing — today's market briefing\n"
        f"  /help — how I work",
        parse_mode="Markdown"
    )


async def cmd_signal(update, context):
    await update.message.reply_text("🔍 Fetching live signals...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        if not r:
            await update.message.reply_text(f"❌ Could not fetch {pair} right now.")
            continue
        direction, strength = interpret_signal(r["score"])
        if direction == "NEUTRAL":
            dp = 2 if "XAU" in pair else 4
            emoji = PAIR_EMOJI.get(pair, "📊")
            await update.message.reply_text(
                f"{emoji} *{pair}* — ⚪ No clear signal\n"
                f"Price: `{r['price']:.{dp}f}` | RSI: `{r['rsi']}`\n"
                f"_Mixed signals — wait for confluence._",
                parse_mode="Markdown"
            )
        else:
            msg = format_signal_message(r["symbol"], r["price"], r["rsi"], r["macd_hist"], r["ma50"], r["score"])
            await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_briefing(update, context):
    await update.message.reply_text("📊 Pulling market data...")
    results = [r for r in (analyse_pair(p) for p in PAIRS) if r]
    if results:
        await update.message.reply_text(format_briefing(results), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Could not fetch data right now. Try again shortly.")


async def cmd_help(update, context):
    await update.message.reply_text(
        "📚 *How D!sForex works*\n\n"
        "I use 3 indicators:\n"
        "  • *RSI* — Is price overbought or oversold?\n"
        "  • *MACD* — Is momentum going up or down?\n"
        "  • *MA50* — What is the overall trend?\n\n"
        "A *strong signal* fires only when all 3 agree.\n\n"
        "⚠️ _I guide your study — I do not trade for you._",
        parse_mode="Markdown"
    )


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
    scheduler.add_job(check_alerts, "cron", hour="7-22", minute=0, args=[bot])
    scheduler.start()

    logger.info("D!sForex bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
