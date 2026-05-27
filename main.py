import discord
from discord.ext import commands, tasks
import requests
import pandas as pd
import random
import feedparser
import os

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")

CHANNELS = {
    "buy": 1509183831289233588,
    "sell": 1509184056498065408,
    "high_profit": 1509191481662115850,
    "scalps": 1509191545071468584,
    "analysis": 1509191576625221702,
    "premium": 1509192066847080519,
    "status": 1509191231857492172,
    "updates": 1509191267026866228,
    "news": 1509191779961016553,
    "welcome": 1509203747840983231
}

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]

# =========================
# BOT SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

last_signal = {}

# =========================
# FUNCTIONS
# =========================

def get_price(symbol):
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
        data = requests.get(url, timeout=5).json()
        return float(data["price"])
    except Exception as e:
        print(f"Error getting price for {symbol}: {e}")
        return None

def get_signal(symbol):
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=15m&limit=100"
        data = requests.get(url, timeout=5).json()

        closes = [float(candle[4]) for candle in data]

        df = pd.DataFrame(closes, columns=["close"])

        ema20 = df["close"].ewm(span=20).mean()
        ema50 = df["close"].ewm(span=50).mean()

        if ema20.iloc[-1] > ema50.iloc[-1]:
            return "BUY"
        else:
            return "SELL"

    except Exception as e:
        print(f"Error getting signal for {symbol}: {e}")
        return None

# =========================
# READY
# =========================

@bot.event
async def on_ready():
    print(f"✅ Bot online: {bot.user}")

    if not auto_signals.is_running():
        auto_signals.start()
    if not auto_scalps.is_running():
        auto_scalps.start()
    if not auto_analysis.is_running():
        auto_analysis.start()
    if not auto_news.is_running():
        auto_news.start()
    if not auto_status.is_running():
        auto_status.start()

# =========================
# WELCOME
# =========================

@bot.event
async def on_member_join(member):

    channel = bot.get_channel(CHANNELS["welcome"])

    if channel is None:
        print(f"Channel not found: {CHANNELS['welcome']}")
        return

    embed = discord.Embed(
        title="👋 BUN VENIT / WELCOME",
        description=f"Salut {member.mention}!\nBine ai venit pe serverul Crypto Signals 🚀",
        color=discord.Color.green()
    )

    embed.add_field(
        name="📘 Informații",
        value="Citește canalele de informații și începe trading-ul!",
        inline=False
    )

    await channel.send(embed=embed)

# =========================
# AUTO SIGNALS
# =========================

@tasks.loop(minutes=15)
async def auto_signals():

    buy_ch = bot.get_channel(CHANNELS["buy"])
    sell_ch = bot.get_channel(CHANNELS["sell"])
    premium_ch = bot.get_channel(CHANNELS["premium"])
    hp_ch = bot.get_channel(CHANNELS["high_profit"])

    if not all([buy_ch, sell_ch, premium_ch, hp_ch]):
        print("⚠️ One or more channels not found")
        return

    for coin in COINS:

        signal = get_signal(coin)

        if signal is None:
            continue

        if last_signal.get(coin) == signal:
            continue

        last_signal[coin] = signal

        price = get_price(coin)

        if price is None:
            continue

        if signal == "BUY":

            embed = discord.Embed(
                title="🚀 BUY SIGNAL",
                color=discord.Color.green()
            )

            embed.add_field(name="🪙 Coin", value=coin)
            embed.add_field(name="💵 Price", value=f"${price:,.2f}")
            embed.add_field(name="📈 Trend", value="Bullish")
            embed.set_footer(text="Crypto Signals Bot")

            await buy_ch.send(embed=embed)

            vip = discord.Embed(
                title="💎 VIP BUY SIGNAL",
                description=f"{coin} bullish breakout detected.",
                color=discord.Color.gold()
            )

            vip.add_field(name="🎯 Target", value="Strong upside potential")
            vip.add_field(name="🛡️ Stop Loss", value="Below EMA50")

            await premium_ch.send(embed=vip)

            if random.randint(1, 3) == 2:

                hp = discord.Embed(
                    title="🔥 HIGH PROFIT SIGNAL",
                    description=f"{coin} strong momentum detected!",
                    color=discord.Color.orange()
                )

                await hp_ch.send(embed=hp)

        else:

            embed = discord.Embed(
                title="📉 SELL SIGNAL",
                color=discord.Color.red()
            )

            embed.add_field(name="🪙 Coin", value=coin)
            embed.add_field(name="💵 Price", value=f"${price:,.2f}")
            embed.add_field(name="📉 Trend", value="Bearish")

            await sell_ch.send(embed=embed)

# =========================
# SCALPS
# =========================

@tasks.loop(minutes=20)
async def auto_scalps():

    ch = bot.get_channel(CHANNELS["scalps"])

    if ch is None:
        print("⚠️ Scalps channel not found")
        return

    coin = random.choice(COINS)

    price = get_price(coin)

    if price is None:
        return

    embed = discord.Embed(
        title="⚡ QUICK SCALP",
        color=discord.Color.orange()
    )

    embed.add_field(name="🪙 Coin", value=coin)
    embed.add_field(name="💵 Price", value=f"${price:,.2f}")
    embed.add_field(name="⏰ Timeframe", value="5m")

    await ch.send(embed=embed)

# =========================
# ANALYSIS
# =========================

@tasks.loop(hours=1)
async def auto_analysis():

    ch = bot.get_channel(CHANNELS["analysis"])

    if ch is None:
        print("⚠️ Analysis channel not found")
        return

    analyses = [
        "📈 BTC bullish above EMA50",
        "🔥 SOL breakout incoming",
        "⚠️ XRP volatility increasing",
        "🚀 ETH strong momentum",
        "📊 BNB trend remains bullish"
    ]

    analysis = random.choice(analyses)

    embed = discord.Embed(
        title="📊 MARKET ANALYSIS",
        description=analysis,
        color=discord.Color.blue()
    )

    await ch.send(embed=embed)

# =========================
# NEWS
# =========================

@tasks.loop(hours=2)
async def auto_news():

    ch = bot.get_channel(CHANNELS["news"])

    if ch is None:
        print("⚠️ News channel not found")
        return

    try:

        feed = feedparser.parse(
            "https://www.coindesk.com/arc/outboundfeeds/rss/"
        )

        if not feed.entries:
            print("⚠️ No news entries found")
            return

        article = feed.entries[0]

        embed = discord.Embed(
            title="📰 CRYPTO NEWS",
            description=article.title,
            url=article.link,
            color=discord.Color.purple()
        )

        embed.add_field(
            name="🌍 Read More",
            value=article.link,
            inline=False
        )

        await ch.send(embed=embed)

    except Exception as e:
        print(f"Error fetching news: {e}")

# =========================
# STATUS
# =========================

@tasks.loop(minutes=30)
async def auto_status():

    statuses = [
        "🚀 Crypto Signals",
        "📈 Monitoring Market",
        "💎 VIP Signals",
        "🔥 BTC ETH SOL XRP"
    ]

    await bot.change_presence(
        activity=discord.Game(random.choice(statuses))
    )

# =========================
# COMMANDS
# =========================

@bot.command()
async def help(ctx):

    embed = discord.Embed(
        title="📘 COMENZI BOT",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="!price btc",
        value="Afișează prețul live",
        inline=False
    )

    embed.add_field(
        name="!semnal eth",
        value="Afișează semnal BUY/SELL",
        inline=False
    )

    await ctx.send(embed=embed)

@bot.command()
async def price(ctx, coin):

    symbol = coin.upper() + "USDT"

    price = get_price(symbol)

    if price is None:
        await ctx.send(f"❌ Error getting price for {symbol}")
        return

    embed = discord.Embed(
        title="💰 LIVE PRICE",
        color=discord.Color.green()
    )

    embed.add_field(name="🪙 Coin", value=symbol)
    embed.add_field(name="💵 Price", value=f"${price:,.2f}")

    await ctx.send(embed=embed)

@bot.command()
async def semnal(ctx, coin):

    symbol = coin.upper() + "USDT"

    signal = get_signal(symbol)

    price = get_price(symbol)

    if signal is None or price is None:
        await ctx.send(f"❌ Error getting signal for {symbol}")
        return

    color = discord.Color.green() if signal == "BUY" else discord.Color.red()

    embed = discord.Embed(
        title=f"📡 {signal} SIGNAL",
        color=color
    )

    embed.add_field(name="🪙 Coin", value=symbol)
    embed.add_field(name="💵 Price", value=f"${price:,.2f}")

    await ctx.send(embed=embed)

# =========================
# START BOT
# =========================

if __name__ == "__main__":
    bot.run(TOKEN)
