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

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "YOUR_TELEGRAM_TOKEN")
TWELVEDATA_API_KEY = os.environ.get("TWELVEDATA_API_KEY", "YOUR_API_KEY")
CHAT_ID = os.environ.get("CHAT_ID", "YOUR_CHAT_ID")

PAIRS = ["EUR/USD", "GBP/USD", "XAU/USD"]
TIMEZONE = pytz.timezone("Africa/Accra")

PAIR_EMOJI = {
    "EUR/USD": "🇪🇺",
    "GBP/USD": "🇬🇧",
    "XAU/USD": "🥇",
}


# ── Data fetching ─────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str = "1h", outputsize: int = 60) -> list[dict] | None:
    """Fetch OHLCV candles from Twelve Data."""
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


def fetch_price(symbol: str) -> float | None:
    """Fetch latest price."""
    url = "https://api.twelvedata.com/price"
    try:
        resp = requests.get(url, params={"symbol": symbol, "apikey": TWELVEDATA_API_KEY}, timeout=10)
        data = resp.json()
        return float(data.get("price", 0)) or None
    except Exception:
        return None


# ── Indicator calculations ─────────────────────────────────────────────────────

def calc_rsi(closes: list[float], period: int = 14) -> float:
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


def calc_ema(closes: list[float], period: int) -> float:
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for price in closes[period:]:
        ema = price * k + ema * (1 - k)
    return ema


def calc_macd(closes: list[float]) -> dict:
    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd_line = ema12 - ema26
    signal_line = macd_line * 0.85
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "hist": histogram}


def calc_ma(closes: list[float], period: int) -> float:
    subset = closes[-period:]
    return sum(subset) / len(subset)


def get_signal_score(rsi: float, macd_hist: float, price: float, ma50: float) -> int:
    """Score from -3 to +3. Positive = bullish, negative = bearish."""
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


def interpret_signal(score: int) -> tuple[str, str]:
    """Return (direction, strength)."""
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


# ── Message formatting ─────────────────────────────────────────────────────────

def format_signal_message(symbol: str, price: float, rsi: float, macd_hist: float, ma50: float, score: int) -> str:
    direction, strength = interpret_signal(score)
    emoji = PAIR_EMOJI.get(symbol, "📊")

    if direction == "BUY":
        signal_line = f"🟢 *{strength.upper()} BUY SIGNAL*"
    elif direction == "SELL":
        signal_line = f"🔴 *{strength.upper()} SELL SIGNAL*"
    else:
        return ""  # Don't send neutral signals as alerts

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
    reasons.append("Price is " + ("above" if price > ma50 else "below") + f" MA50 ({ma50:.4f})")

    reason_text = "\n".join(f"  • {r}" for r in reasons)

    dp = 2 if "XAU" in symbol else 4
    price_str = f"{price:.{dp}f}"

    now = datetime.now(TIMEZONE).strftime("%H:%M WAT")

    return (
        f"{emoji} *{symbol}* — {signal_line}\n"
        f"Price: `{price_str}`\n\n"
        f"Why:\n{reason_text}\n\n"
        f"⚠️ _Study this signal, not financial advice._\n"
        f"🕐 _{now}_"
    )


def format_briefing_message(results: list[dict]) -> str:
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

        lines.append(
            f"{emoji} *{sym}*\n"
            f"  Price: `{r['price']:.{dp}f}` | RSI: `{r['rsi']}`\n"
            f"  {mood}\n"
        )

    lines.append("_Use /signal <pair> for full breakdown_")
    return "\n".join(lines)


def format_eod_message(results: list[dict]) -> str:
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y")
    lines = [f"🌙 *End of Day Recap — {now}*\n"]

    buys = [r for r in results if interpret_signal(r["score"])[0] == "BUY"]
    sells = [r for r in results if interpret_signal(r["score"])[0] == "SELL"]
    neutrals = [r for r in results if interpret_signal(r["score"])[0] == "NEUTRAL"]

    if buys:
        lines.append("🟢 *Bullish today:* " + ", ".join(r["symbol"] for r in buys))
    if sells:
        lines.append("🔴 *Bearish today:* " + ", ".join(r["symbol"] for r in sells))
    if neutrals:
        lines.append("⚪ *No clear signal:* " + ", ".join(r["symbol"] for r in neutrals))

    lines.append("\n_Open your Exness demo and review these against what the market actually did. That's how you learn._ 📚")
    return "\n".join(lines)


# ── Core analysis ──────────────────────────────────────────────────────────────

def analyse_pair(symbol: str) -> dict | None:
    candles = fetch_candles(symbol)
    if not candles or len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
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
    }


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def send_morning_briefing(bot: Bot):
    logger.info("Sending morning briefing...")
    results = []
    for pair in PAIRS:
        r = analyse_pair(pair)
        if r:
            results.append(r)
    if results:
        msg = format_briefing_message(results)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")


async def send_eod_recap(bot: Bot):
    logger.info("Sending EOD recap...")
    results = []
    for pair in PAIRS:
        r = analyse_pair(pair)
        if r:
            results.append(r)
    if results:
        msg = format_eod_message(results)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")


async def check_alerts(bot: Bot):
    logger.info("Checking for strong signals...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        if not r:
            continue
        direction, strength = interpret_signal(r["score"])
        if strength == "strong" and direction != "NEUTRAL":
            msg = format_signal_message(
                r["symbol"], r["price"], r["rsi"],
                r["macd_hist"], r["ma50"], r["score"]
            )
            if msg:
                await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                logger.info(f"Alert sent for {pair}: {direction}")


# ── Telegram command handlers ──────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "trader"
    await update.message.reply_text(
        f"👋 Hey {name}! I'm your Forex Signal Guide.\n\n"
        f"I watch *{', '.join(PAIRS)}* and alert you when signals look strong.\n\n"
        f"Commands:\n"
        f"  /signal — check all pairs now\n"
        f"  /signal EURUSD — check one pair\n"
        f"  /briefing — get today's briefing\n"
        f"  /help — show this message",
        parse_mode="Markdown"
    )


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    pairs_to_check = PAIRS

    if args:
        raw = args[0].upper().replace("USD", "/USD").replace("EUR", "EUR/").replace("GBP", "GBP/").replace("XAU", "XAU/")
        # simple normalisation
        lookup = {p.replace("/", ""): p for p in PAIRS}
        clean = args[0].upper().replace("/", "")
        if clean in lookup:
            pairs_to_check = [lookup[clean]]
        else:
            await update.message.reply_text(f"Unknown pair. Available: {', '.join(PAIRS)}")
            return

    await update.message.reply_text("🔍 Fetching live signals...")
    for pair in pairs_to_check:
        r = analyse_pair(pair)
        if not r:
            await update.message.reply_text(f"❌ Could not fetch data for {pair}. Try again shortly.")
            continue
        direction, strength = interpret_signal(r["score"])
        if direction == "NEUTRAL":
            dp = 2 if "XAU" in pair else 4
            emoji = PAIR_EMOJI.get(pair, "📊")
            await update.message.reply_text(
                f"{emoji} *{pair}* — ⚪ No clear signal\n"
                f"Price: `{r['price']:.{dp}f}` | RSI: `{r['rsi']}`\n"
                f"_Indicators are mixed. Wait for confluence._",
                parse_mode="Markdown"
            )
        else:
            msg = format_signal_message(r["symbol"], r["price"], r["rsi"], r["macd_hist"], r["ma50"], r["score"])
            await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Pulling market data...")
    results = []
    for pair in PAIRS:
        r = analyse_pair(pair)
        if r:
            results.append(r)
    if results:
        await update.message.reply_text(format_briefing_message(results), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Could not fetch data right now. Try again in a moment.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *How I work*\n\n"
        "I use 3 indicators to study the market:\n"
        "  • *RSI* — Is a currency overbought or oversold?\n"
        "  • *MACD* — Is momentum going up or down?\n"
        "  • *MA50* — What's the overall trend?\n\n"
        "A *strong signal* fires only when all 3 agree.\n\n"
        "⚠️ _I guide you to study signals — I don't trade for you, and this is not financial advice._",
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
    # Morning briefing — 7am WAT
    scheduler.add_job(send_morning_briefing, "cron", hour=7, minute=0, args=[bot])
    # EOD recap — 9pm WAT
    scheduler.add_job(send_eod_recap, "cron", hour=21, minute=0, args=[bot])
    # Alert checks — every 2 hours during market hours
    scheduler.add_job(check_alerts, "cron", hour="7-22", minute=0, args=[bot])
    scheduler.start()

    logger.info("Bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
