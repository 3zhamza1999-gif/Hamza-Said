import os
import requests
import pandas as pd
import yfinance as yf
import feedparser
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ["BOT_TOKEN"]

def format_symbol(coin):
    coin = coin.upper()
    if coin in ["GOLD", "XAU", "XAUUSD", "XAU/USD"]:
        return "GOLD"
    return coin

def get_price(coin):
    sym = format_symbol(coin)
    try:
        if sym == "US30":
            data = yf.Ticker("^DJI").history(period="1d", interval="1m")
            if not data.empty:
                return float(data['Close'].iloc[-1])
        elif sym == "GOLD":
            data = yf.Ticker("GC=F").history(period="1d", interval="1m") # عقود الذهب الآجلة
            if not data.empty:
                return float(data['Close'].iloc[-1])
        
        r = requests.get("https://api.binance.com/api/v3/ticker/price",
                         params={"symbol": sym + "USDT"}).json()
        return float(r["price"]) if "price" in r else None
    except:
        return None

def get_market_news():
    try:
        feed = feedparser.parse("https://www.investing.com/rss/news_25.rss")
        news_list = []
        for entry in feed.entries[:5]:
            news_list.append(f"• {entry.title}")
        return "\n".join(news_list) if news_list else "لا توجد أخبار جديدة حالياً."
    except:
        return "تعذر جلب الأخبار حالياً."

def klines(sym, tf="4h", n=300):
    sym = format_symbol(sym)
    if sym in ["US30", "GOLD"]:
        ticker_symbol = "^DJI" if sym == "US30" else "GC=F"
        tf_map = {"5m": "5m", "15m": "15m", "1h": "60m", "4h": "1h"}
        y_tf = tf_map.get(tf, "60m")
        df = yf.Ticker(ticker_symbol).history(period="60d", interval=y_tf)
        if df.empty:
            return pd.DataFrame()
        df = df.tail(n).reset_index()
        df.columns = [c.lower() for c in df.columns]
        rename_dict = {}
        for col in df.columns:
            if 'datetime' in col or 'date' in col:
                rename_dict[col] = 't'
        df = df.rename(columns={**rename_dict, 'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c', 'volume': 'v'})
        for k in "ohlcv":
            if k in df.columns:
                df[k] = df[k].astype(float)
        return df
    else:
        r = requests.get("https://api.binance.com/api/v3/klines",
            params={"symbol": sym + "USDT", "interval": tf, "limit": n}).json()
        if isinstance(r, dict) and 'code' in r:
            return pd.DataFrame()
        df = pd.DataFrame(r, columns=["t","o","h","l","c","v","ct","qv","n","tb","tq","i"])
        for k in "ohlcv":
            df[k] = df[k].astype(float)
        return df

def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n).mean()
    return 100 - 100 / (1 + up / dn)

def swings(df, k=5):
    H, L = [], []
    if len(df) < k * 2 + 1:
        return H, L
    for i in range(k, len(df) - k):
        if df.h.iloc[i] == df.h.iloc[i-k:i+k+1].max(): H.append((i, df.h.iloc[i]))
        if df.l.iloc[i] == df.l.iloc[i-k:i+k+1].min(): L.append((i, df.l.iloc[i]))
    return H, L

def run_analysis(sym, tf="4h"):
    sym = format_symbol(sym)
    df = klines(sym, tf)
    if df.empty or len(df) < 30:
        raise ValueError("بيانات غير كافية")
    c = df.c
    p = c.iloc[-1]
    score = 0
    e50 = c.ewm(span=50).mean().iloc[-1]
    e200 = c.ewm(span=200).mean().iloc[-1]
    rs = rsi(c).iloc[-1]
    
    score += 1 if p > e50 else -1
    score += 1 if e50 > e200 else -1
    score += 1 if rs > 50 else -1
    
    lines = [f"📊 {sym} ({tf}) | السعر: {p:,.2f}",
             f"• فني & اتجاه: RSI {rs:.0f} | EMA50/200"]

    H, L = swings(df)
    struct, bos = "جانبي", "لا يوجد"
    if len(H) >= 2 and len(L) >= 2:
        hh, hl = H[-1][1] > H[-2][1], L[-1][1] > L[-2][1]
        if hh and hl: struct = "SMC صاعد (HH/HL)"; score += 1
        elif not hh and not hl: struct = "SMC هابط (LH/LL)"; score -= 1
    if H and p > H[-1][1]: bos = "BOS صاعد"; score += 1
    elif L and p < L[-1][1]: bos = "BOS هابط"; score -= 1
    lines.append(f"• SMC & Price Action: {struct} | {bos}")

    gap = "لا يوجد FVG"
    if len(df) >= 43:
        for i in range(len(df) - 30, len(df) - 3):
            if df.l.iloc[i+2] > df.h.iloc[i]:
                gap = f"ICT FVG صاعد"
            elif df.h.iloc[i+2] < df.l.iloc[i]:
                gap = f"ICT FVG هابط"
    lines.append(f"• ICT FVG: {gap}")

    if score >= 2: verdict = "صاعد 🟢"
    elif score <= -2: verdict = "هابط 🔴"
    else: verdict = "محايد ⚪"
    
    lines.append(f"النتيجة: {score:+d} → {verdict}")
    return score, p, "\n".join(lines)

COINS = ["US30", "GOLD", "BTC", "ETH"]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📊 US30", callback_data="signal_US30"),
            InlineKeyboardButton("🪙 الذهب GOLD", callback_data="signal_GOLD"),
            InlineKeyboardButton("₿ بتكوين BTC", callback_data="signal_BTC"),
        ],
        [
            InlineKeyboardButton("📰 الأخبار", callback_data="news"),
            InlineKeyboardButton("🔍 مسح الأسواق", callback_data="scan"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    welcome_text = (
        "🤖 **مرحباً بك في بوت التداول الاحترافي المدمج (ICT, SMC, Price Action & News)**\n\n"
        "اختر أحد الأصول أدناه للحصول على التقرير الشامل، أو استخدم الأوامر المباشرة:"
    )
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.callback_query:
        await update.callback_query.message.reply_text(welcome_text, reply_markup=reply_markup, parse_mode="Markdown")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("signal_"):
        coin = data.split("_")[1]
        await query.message.reply_text(f"⏳ جاري تحليل {coin} عبر مدارس ICT, SMC و Price Action...")
        await process_signal(query.message, coin)
    elif data == "news":
        news_text = get_market_news()
        await query.message.reply_text(f"📰 **أبرز الأخبار الاقتصادية والأساسية:**\n\n{news_text}", parse_mode="Markdown")
    elif data == "scan":
        await query.message.reply_text("🔍 جاري مسح الأسواق وإعداد التقرير...")
        await process_scan(query.message)

async def process_signal(message, coin):
    sym = format_symbol(coin)
    timeframes = ["4h", "1h", "15m", "5m"]
    total_score = 0
    current_price = 0
    msg_list = [f"🚀 **تقرير التوصيات الشامل لـ {sym} (ICT & SMC)**\n"]

    try:
        for tf in timeframes:
            score, p, details = run_analysis(sym, tf)
            total_score += score
            current_price = p
            msg_list.append(details + f"\n-------------------")

        account_balance = 10000
        risk_percent = 1.0
        
        if total_score >= 4:
            recommendation = "🟢 **توصية شراء قوية (LONG)**"
            tp = current_price * 1.012
            sl = current_price * 0.994
        elif total_score <= -4:
            recommendation = "🔴 **توصية بيع قوية (SHORT)**"
            tp = current_price * 0.988
            sl = current_price * 1.006
        else:
            recommendation = "⚪ **السوق متذبذب - انتظر تأكيد**"
            tp, sl = 0, 0

        summary_msg = (
            f"\n🎯 **الخلاصة وإدارة المخاطر:**\n"
            f"• مجموع النقاط: {total_score:+d}\n"
            f"• القرار: {recommendation}\n"
        )
        if total_score >= 4 or total_score <= -4:
            risk_amount = account_balance * (risk_percent / 100)
            summary_msg += (
                f"• الهدف (TP): {tp:,.2f}\n"
                f"• وقف الخسارة (SL): {sl:,.2f}\n"
                f"• مقترح إدارة المخاطر (مخاطرة 1%): مبلغ المخاطرة = ${risk_amount:,.2f}"
            )

        msg_list.append(summary_msg)
        await message.reply_text("\n".join(msg_list), parse_mode="Markdown")
    except Exception as e:
        await message.reply_text(f"❌ حدث خطأ أثناء تحليل {sym}: {str(e)}")

async def process_scan(message):
    res = []
    for coin in COINS:
        try:
            score, _, _ = run_analysis(coin, "4h")
            res.append((score, coin))
        except:
            pass
    res.sort(key=lambda x: -abs(x[0]))
    msg = "🔍 **أقوى الفرص الحالية في السوق:**\n" + "\n".join(
        f"• {c}: {s:+d} {'🟢' if s > 0 else '🔴' if s < 0 else '⚪'}" for s, c in res)
    await message.reply_text(msg, parse_mode="Markdown")

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "US30").upper()
    await process_signal(update.message, coin)

async def news_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    news_text = get_market_news()
    await update.message.reply_text(f"📰 **أبرز الأخبار الاقتصادية والأساسية:**\n\n{news_text}", parse_mode="Markdown")

async def scan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_scan(update.message)

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("signal", signal_cmd))
app.add_handler(CommandHandler("news", news_cmd))
app.add_handler(CommandHandler("scan", scan_cmd))
app.add_handler(CallbackQueryHandler(button_handler))

app.run_polling()
