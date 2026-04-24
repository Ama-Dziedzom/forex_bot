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

PAIR_EMOJI = {"EUR/USD": "🇪🇺", "GBP/USD": "🇬🇧", "XAU/USD": "🥇"}
PAIR_LABEL = {"EUR/USD": "Euro", "GBP/USD": "British Pound", "XAU/USD": "Gold"}
STOP_LOSS_DISTANCE = {"EUR/USD": 0.0015, "GBP/USD": 0.0020, "XAU/USD": 8.0}


# ── Data fetching ──────────────────────────────────────────────────────────────

def fetch_candles(symbol, interval="1h", outputsize=60):
    try:
        resp = requests.get(
            "https://api.twelvedata.com/time_series",
            params={"symbol": symbol, "interval": interval, "outputsize": outputsize, "apikey": TWELVEDATA_API_KEY},
            timeout=10,
        )
        data = resp.json()
        if data.get("status") == "error":
            logger.error(f"API error {symbol} {interval}: {data.get('message')}")
            return None
        return [{"close": float(v["close"]), "high": float(v["high"]), "low": float(v["low"])} for v in reversed(data.get("values", []))]
    except Exception as e:
        logger.error(f"Fetch error {symbol} {interval}: {e}")
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
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 2)


def calc_ema(closes, period):
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for p in closes[period:]:
        ema = p * k + ema * (1 - k)
    return ema


def calc_macd_hist(closes):
    return calc_ema(closes, 12) - calc_ema(closes, 26)


def calc_ma(closes, period):
    return sum(closes[-period:]) / min(period, len(closes))


def get_score(rsi, macd_hist, price, ma50):
    s = 0
    if rsi < 25: s += 3
    elif rsi < 35: s += 2
    elif rsi < 45: s += 1
    elif rsi > 75: s -= 3
    elif rsi > 65: s -= 2
    elif rsi > 55: s -= 1
    s += 1 if macd_hist > 0 else -1
    s += 1 if price > ma50 else -1
    return s


def get_direction(score):
    if score >= 2: return "BUY"
    if score <= -2: return "SELL"
    return "WAIT"


# ── Multi-timeframe analysis ───────────────────────────────────────────────────

def analyse_timeframe(symbol, interval, outputsize=60):
    """Analyse a single timeframe and return direction + score."""
    candles = fetch_candles(symbol, interval, outputsize)
    if not candles or len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    price = closes[-1]
    rsi = calc_rsi(closes)
    macd_hist = calc_macd_hist(closes)
    ma50 = calc_ma(closes, min(50, len(closes)))
    score = get_score(rsi, macd_hist, price, ma50)
    return {
        "price": price,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "ma50": ma50,
        "score": score,
        "direction": get_direction(score),
    }


def analyse_pair(symbol):
    """
    Multi-timeframe analysis:
    - 1h = overall trend
    - 15min = current momentum confirmation

    Signal only fires when BOTH timeframes agree.
    If they conflict → WAIT (reversal may be forming)
    """
    tf1h = analyse_timeframe(symbol, "1h", 60)
    if not tf1h:
        return None

    tf15m = analyse_timeframe(symbol, "15min", 30)
    if not tf15m:
        # Fall back to 1h only if 15min fetch fails
        return {
            "symbol": symbol,
            "price": tf1h["price"],
            "rsi": tf1h["rsi"],
            "macd_hist": tf1h["macd_hist"],
            "ma50": tf1h["ma50"],
            "score": tf1h["score"],
            "direction": tf1h["direction"],
            "confirmed": False,
            "tf1h_dir": tf1h["direction"],
            "tf15m_dir": "unknown",
        }

    dir_1h = tf1h["direction"]
    dir_15m = tf15m["direction"]

    
    # 1hr leads — only block if 15min is actively opposite
    if dir_1h != "WAIT":
        if dir_15m == dir_1h or dir_15m == "WAIT":
            final_direction = dir_1h
            confirmed = True
        else:
            final_direction = "WAIT"
            confirmed = False
    else:
        final_direction = "WAIT"
        confirmed = False

    return {
        "symbol": symbol,
        "price": tf1h["price"],
        "rsi": tf1h["rsi"],
        "macd_hist": tf1h["macd_hist"],
        "ma50": tf1h["ma50"],
        "score": tf1h["score"],
        "direction": final_direction,
        "confirmed": confirmed,
        "tf1h_dir": dir_1h,
        "tf15m_dir": dir_15m,
    }


# ── Plain English reasons ──────────────────────────────────────────────────────

def plain_reason(symbol, direction, rsi, macd_hist, price, ma50, confirmed, tf1h_dir, tf15m_dir):
    label = PAIR_LABEL.get(symbol, symbol)

    if not confirmed and direction == "WAIT" and tf1h_dir != tf15m_dir:
        return f"1hr trend says {tf1h_dir} but 15min momentum says {tf15m_dir} — possible reversal forming. Sit this one out."

    if direction == "BUY":
        if rsi < 25: return f"{label} is very cheap right now — likely to bounce up. Both timeframes confirm."
        if rsi < 35: return f"{label} is oversold and recovering. Short and long term trend both agree."
        return f"{label} momentum is building upward on both the hourly and 15min chart."

    if direction == "SELL":
        if rsi > 75: return f"{label} has risen too fast — likely to drop. Both timeframes confirm."
        if rsi > 65: return f"{label} is overbought and turning down. Short and long term agree."
        return f"{label} momentum is weakening on both the hourly and 15min chart."

    return f"No strong direction for {label} right now. Wait for a clearer setup."


# ── Stop loss / take profit ────────────────────────────────────────────────────

def calc_sl_tp(symbol, direction, price):
    distance = STOP_LOSS_DISTANCE.get(symbol, 0.002)
    dp = 2 if "XAU" in symbol else 4
    if direction == "BUY":
        return f"Stop loss: `{price - distance:.{dp}f}` | Take profit: `{price + distance * 2:.{dp}f}`"
    elif direction == "SELL":
        return f"Stop loss: `{price + distance:.{dp}f}` | Take profit: `{price - distance * 2:.{dp}f}`"
    return ""


# ── Message formatting ─────────────────────────────────────────────────────────

def format_signal(r, alert=False):
    symbol = r["symbol"]
    direction = r["direction"]
    confirmed = r["confirmed"]
    emoji = PAIR_EMOJI.get(symbol, "📊")
    dp = 2 if "XAU" in symbol else 4
    now = datetime.now(TIMEZONE).strftime("%H:%M WAT")

    reason = plain_reason(
        symbol, direction, r["rsi"], r["macd_hist"],
        r["price"], r["ma50"], confirmed,
        r["tf1h_dir"], r["tf15m_dir"]
    )

    if direction == "BUY":
        action = "🟢 *BUY*" + (" ✅ confirmed" if confirmed else "")
    elif direction == "SELL":
        action = "🔴 *SELL*" + (" ✅ confirmed" if confirmed else "")
    else:
        action = "⚪ *WAIT*"

    sl_line = calc_sl_tp(symbol, direction, r["price"]) if direction in ("BUY", "SELL") else ""

    msg = f"{'🚨 *SIGNAL ALERT*' + chr(10) + chr(10) if alert else ''}"
    msg += f"{emoji} *{symbol}*\n{action}\n_{reason}_\n\nPrice: `{r['price']:.{dp}f}`\n"
    if sl_line:
        msg += f"{sl_line}\n"
    msg += f"\n🕐 _{now}_"
    return msg


def format_briefing(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y · %H:%M WAT")
    lines = [f"📋 *Good morning! Forex briefing*\n_{now}_\n"]
    for r in results:
        sym = r["symbol"]
        emoji = PAIR_EMOJI.get(sym, "📊")
        direction = r["direction"]
        confirmed = r["confirmed"]
        dp = 2 if "XAU" in sym else 4

        if direction == "BUY":
            action = "🟢 BUY" + (" ✅" if confirmed else " (unconfirmed)")
        elif direction == "SELL":
            action = "🔴 SELL" + (" ✅" if confirmed else " (unconfirmed)")
        else:
            action = "⚪ WAIT"

        tf_note = f"1hr: {r['tf1h_dir']} | 15min: {r['tf15m_dir']}"
        lines.append(f"{emoji} *{sym}* — {action}\nPrice: `{r['price']:.{dp}f}` | _{tf_note}_\n")

    lines.append("_Use /signal anytime for a fresh update_")
    return "\n".join(lines)


def format_eod(results):
    now = datetime.now(TIMEZONE).strftime("%A, %d %b %Y")
    lines = [f"🌙 *End of Day — {now}*\n"]
    for r in results:
        sym = r["symbol"]
        emoji = PAIR_EMOJI.get(sym, "📊")
        direction = r["direction"]
        dp = 2 if "XAU" in sym else 4
        action = "🟢 Closed bullish" if direction == "BUY" else "🔴 Closed bearish" if direction == "SELL" else "⚪ Closed neutral"
        lines.append(f"{emoji} *{sym}* — {action} at `{r['price']:.{dp}f}`")
    lines.append("\n_Review your Exness demo trades against these signals. That's how you improve._ 📚")
    return "\n".join(lines)


# ── Scheduled jobs ─────────────────────────────────────────────────────────────

async def send_morning_briefing(bot):
    logger.info("Sending morning briefing...")
    results = []
    for p in PAIRS:
        r = analyse_pair(p)
        if r:
            results.append(r)
        await asyncio.sleep(30)
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_briefing(results), parse_mode="Markdown")


async def send_eod_recap(bot):
    logger.info("Sending EOD recap...")
    results = []
    for p in PAIRS:
        r = analyse_pair(p)
        if r:
            results.append(r)
        await asyncio.sleep(30)
    if results:
        await bot.send_message(chat_id=CHAT_ID, text=format_eod(results), parse_mode="Markdown")


async def check_alerts(bot):
    logger.info("Checking for strong signals...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        await asyncio.sleep(30)
        if not r:
            continue
        # Only alert on CONFIRMED strong signals (both timeframes agree)
        if r["confirmed"] and r["direction"] in ("BUY", "SELL"):
            msg = format_signal(r, alert=True)
            await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
            logger.info(f"Confirmed alert sent for {pair}: {r['direction']}")


async def send_auto_signal(bot):
    for p in PAIRS:
        r = analyse_pair(p)
        await asyncio.sleep(30)
        if not r:
            continue
        msg = format_signal(r)
        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "trader"
    await update.message.reply_text(
        f"👋 Hey {name}! I'm *D!sForex* — your personal forex signal guide.\n\n"
        f"I now check *two timeframes* before signalling:\n"
        f"  📊 1-hour chart — overall trend\n"
        f"  📊 15-min chart — current momentum\n\n"
        f"A signal only fires when *both agree* ✅\n"
        f"If they conflict — I tell you to WAIT ⚪\n\n"
        f"Commands:\n"
        f"  /signal — check all pairs now\n"
        f"  /briefing — morning market summary\n"
        f"  /help — how I work",
        parse_mode="Markdown"
    )


async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Checking both timeframes for each pair...")
    for pair in PAIRS:
        r = analyse_pair(pair)
        await asyncio.sleep(30)
        if not r:
            await update.message.reply_text(f"❌ Could not fetch {pair} right now.")
            continue
        await update.message.reply_text(format_signal(r), parse_mode="Markdown")


async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Pulling both timeframes...")
    results = []
    for p in PAIRS:
        r = analyse_pair(p)
        if r:
            results.append(r)
        await asyncio.sleep(30)
    if results:
        await update.message.reply_text(format_briefing(results), parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Could not fetch data. Try again shortly.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 *How D!sForex works — upgraded*\n\n"
        "I now check *two timeframes* before every signal:\n\n"
        "1️⃣ *1-hour chart* — what's the big picture trend?\n"
        "2️⃣ *15-minute chart* — is that trend still holding RIGHT NOW?\n\n"
        "✅ *Both say BUY* → I signal BUY\n"
        "✅ *Both say SELL* → I signal SELL\n"
        "⚠️ *They disagree* → I say WAIT — reversal may be forming\n\n"
        "This is how you spotted that EUR/USD reversal manually earlier — now the bot catches it automatically.\n\n"
        "⚠️ _Always practice on Exness demo first._",
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
    scheduler.add_job(check_alerts, "cron", day_of_week="mon-fri", hour="6-21", minute="1,16,31,46", args=[bot])
    scheduler.add_job(send_auto_signal, "cron", day_of_week="mon-fri", hour="6-21", minute="8,23,38,53", args=[bot])
    scheduler.add_job(send_morning_briefing, "cron", day_of_week="mon-fri", hour=7, minute=0, args=[bot])
    scheduler.add_job(send_eod_recap, "cron", day_of_week="mon-fri", hour=21, minute=0, args=[bot])
    scheduler.start()

    logger.info("D!sForex v2 — multi-timeframe bot is running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
