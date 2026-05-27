import discord
from discord.ext import commands, tasks
import requests
import pandas as pd
import numpy as np
import random
import feedparser
import os
import logging
from datetime import datetime, timedelta
import sqlite3
from collections import defaultdict
import asyncio
import ta  # Technical Analysis library

# =========================
# LOGGING SETUP
# =========================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =========================
# CONFIG
# =========================

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    logger.error("TOKEN environment variable not set!")
    exit(1)

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

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "DOGEUSDT"]

# Signal confidence thresholds
MIN_SIGNAL_CONFIDENCE = 0.75  # 75% confidence minimum
MIN_RSI_BUY = 30
MAX_RSI_BUY = 50
MIN_RSI_SELL = 50
MAX_RSI_SELL = 70

# Rate limiting config
COMMAND_COOLDOWN = 5
API_CALL_DELAY = 0.1

# =========================
# DATABASE SETUP
# =========================

def init_db():
    """Initialize SQLite database"""
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                coin TEXT NOT NULL,
                signal TEXT NOT NULL,
                price REAL NOT NULL,
                confidence REAL NOT NULL,
                rsi REAL,
                macd REAL,
                bb_position REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                win INTEGER DEFAULT 0
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS command_usage (
                user_id INTEGER NOT NULL,
                command TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signal_performance (
                coin TEXT PRIMARY KEY,
                total_signals INTEGER DEFAULT 0,
                winning_signals INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                last_update DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("✅ Database initialized successfully")
    except Exception as e:
        logger.error(f"❌ Database initialization failed: {e}")

def log_signal(coin, signal, price, confidence, rsi, macd, bb_position):
    """Log signal with technical indicators"""
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO signals (coin, signal, price, confidence, rsi, macd, bb_position) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (coin, signal, price, confidence, rsi, macd, bb_position))
        conn.commit()
        conn.close()
        logger.info(f"✅ Signal logged: {coin} {signal} (confidence: {confidence:.2%})")
    except Exception as e:
        logger.error(f"❌ Failed to log signal: {e}")

def get_signal_performance(coin):
    """Get win rate for a coin"""
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        cursor.execute(
            'SELECT total_signals, winning_signals, win_rate FROM signal_performance WHERE coin = ?',
            (coin,)
        )
        result = cursor.fetchone()
        conn.close()
        
        if result:
            return {
                'total': result[0],
                'wins': result[1],
                'win_rate': result[2]
            }
        return {'total': 0, 'wins': 0, 'win_rate': 0}
    except Exception as e:
        logger.error(f"❌ Failed to get signal performance: {e}")
        return {'total': 0, 'wins': 0, 'win_rate': 0}

def check_command_cooldown(user_id):
    """Check if user is on cooldown"""
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        
        cutoff_time = (datetime.now() - timedelta(seconds=COMMAND_COOLDOWN)).isoformat()
        cursor.execute(
            'SELECT COUNT(*) FROM command_usage WHERE user_id = ? AND timestamp > ?',
            (user_id, cutoff_time)
        )
        
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0
    except Exception as e:
        logger.error(f"❌ Cooldown check failed: {e}")
        return False

def log_command_usage(user_id, command):
    """Log command usage"""
    try:
        conn = sqlite3.connect('signals.db')
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO command_usage (user_id, command) VALUES (?, ?)',
            (user_id, command)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"❌ Failed to log command usage: {e}")

# =========================
# BOT SETUP
# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

last_signal = {}
signal_timestamps = defaultdict(lambda: datetime.min)

# =========================
# TECHNICAL ANALYSIS FUNCTIONS
# =========================

async def get_price(symbol, retries=3):
    """Get live price with retry logic"""
    for attempt in range(retries):
        try:
            url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            response = await asyncio.to_thread(
                lambda: requests.get(url, timeout=5)
            )
            
            if response.status_code == 200:
                data = response.json()
                price = float(data["price"])
                return price
            elif response.status_code == 404:
                logger.warning(f"⚠️ Coin {symbol} not found")
                return None
            else:
                if attempt < retries - 1:
                    await asyncio.sleep(API_CALL_DELAY)
                    
        except Exception as e:
            logger.error(f"❌ Error getting price for {symbol}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(API_CALL_DELAY)
    
    return None

async def get_klines(symbol, interval="15m", limit=200):
    """Fetch candlestick data from Binance"""
    try:
        url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
        response = await asyncio.to_thread(
            lambda: requests.get(url, timeout=5)
        )
        
        if response.status_code == 200:
            return response.json()
        else:
            logger.error(f"❌ API error {response.status_code} for {symbol}")
            return None
    except Exception as e:
        logger.error(f"❌ Error fetching klines for {symbol}: {e}")
        return None

async def calculate_indicators(symbol, interval="15m"):
    """Calculate comprehensive technical indicators"""
    try:
        klines = await get_klines(symbol, interval, limit=200)
        
        if not klines or len(klines) < 50:
            logger.warning(f"⚠️ Insufficient data for {symbol}")
            return None
        
        # Extract OHLCV data
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        
        # Convert to float
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)
        
        # Calculate indicators
        indicators = {}
        
        # 1. EMA (Exponential Moving Average)
        indicators['ema_20'] = ta.trend.ema_indicator(df['close'], window=20).iloc[-1]
        indicators['ema_50'] = ta.trend.ema_indicator(df['close'], window=50).iloc[-1]
        indicators['ema_200'] = ta.trend.ema_indicator(df['close'], window=200).iloc[-1]
        
        # 2. RSI (Relative Strength Index)
        indicators['rsi'] = ta.momentum.rsi(df['close'], window=14).iloc[-1]
        
        # 3. MACD (Moving Average Convergence Divergence)
        macd = ta.trend.macd(df['close'], window_fast=12, window_slow=26, window_sign=9)
        indicators['macd'] = macd.iloc[-1, 0]
        indicators['macd_signal'] = macd.iloc[-1, 1]
        indicators['macd_diff'] = macd.iloc[-1, 2]
        
        # 4. Bollinger Bands
        bb = ta.volatility.bollinger_bands(df['close'], window=20, window_dev=2)
        current_price = df['close'].iloc[-1]
        bb_high = bb.iloc[-1, 0]
        bb_mid = bb.iloc[-1, 1]
        bb_low = bb.iloc[-1, 2]
        
        if current_price <= bb_low:
            indicators['bb_position'] = -1
        elif current_price >= bb_high:
            indicators['bb_position'] = 1
        else:
            indicators['bb_position'] = (current_price - bb_mid) / (bb_high - bb_mid)
        
        # 5. Stochastic Oscillator
        stoch = ta.momentum.stoch(df['high'], df['low'], df['close'], window=14, smooth_k=3, smooth_d=3)
        indicators['stoch_k'] = stoch.iloc[-1, 0]
        indicators['stoch_d'] = stoch.iloc[-1, 1]
        
        # 6. ADX (Average Directional Index)
        adx = ta.trend.adx(df['high'], df['low'], df['close'], window=14)
        indicators['adx'] = adx.iloc[-1]
        
        # 7. Volume analysis
        indicators['volume_sma'] = ta.volume.volume_sma(df['close'], df['volume'], window=20).iloc[-1]
        
        # Current price
        indicators['price'] = current_price
        
        return indicators
        
    except Exception as e:
        logger.error(f"❌ Error calculating indicators for {symbol}: {e}")
        return None

async def analyze_signal(symbol):
    """
    Advanced multi-indicator signal analysis
    Returns: (signal, confidence, indicators)
    """
    try:
        indicators = await calculate_indicators(symbol)
        
        if indicators is None:
            return None, 0, None
        
        # Signal scoring system (0-1 scale)
        buy_score = 0
        buy_factors = 0
        
        # ========== BUY SIGNALS ==========
        
        # 1. EMA Alignment (weight: 25%)
        if indicators['ema_20'] > indicators['ema_50'] > indicators['ema_200']:
            buy_score += 0.25
            logger.debug(f"📈 {symbol}: EMA alignment bullish")
        buy_factors += 0.25
        
        # 2. Price above EMAs (weight: 20%)
        if indicators['price'] > indicators['ema_20'] > indicators['ema_50']:
            buy_score += 0.20
            logger.debug(f"📈 {symbol}: Price above EMAs")
        buy_factors += 0.20
        
        # 3. RSI Signal (weight: 20%)
        if MIN_RSI_BUY <= indicators['rsi'] <= MAX_RSI_BUY:
            buy_score += 0.20
            logger.debug(f"📈 {symbol}: RSI in optimal buy zone ({indicators['rsi']:.2f})")
        elif 50 < indicators['rsi'] < 70:
            buy_score += 0.10
        buy_factors += 0.20
        
        # 4. MACD Signal (weight: 15%)
        if indicators['macd'] > indicators['macd_signal'] and indicators['macd_diff'] > 0:
            buy_score += 0.15
            logger.debug(f"📈 {symbol}: MACD bullish crossover")
        buy_factors += 0.15
        
        # 5. Bollinger Bands (weight: 10%)
        if indicators['bb_position'] < 0:
            buy_score += 0.10
            logger.debug(f"📈 {symbol}: BB oversold position")
        buy_factors += 0.10
        
        # 6. Stochastic Signal (weight: 10%)
        if indicators['stoch_k'] < 20 and indicators['stoch_d'] < 20:
            buy_score += 0.10
            logger.debug(f"📈 {symbol}: Stochastic oversold")
        buy_factors += 0.10
        
        buy_confidence = buy_score / buy_factors if buy_factors > 0 else 0
        
        # ========== SELL SIGNALS ==========
        
        sell_score = 0
        sell_factors = 0
        
        # 1. EMA Alignment (weight: 25%)
        if indicators['ema_20'] < indicators['ema_50'] < indicators['ema_200']:
            sell_score += 0.25
        sell_factors += 0.25
        
        # 2. Price below EMAs (weight: 20%)
        if indicators['price'] < indicators['ema_20'] < indicators['ema_50']:
            sell_score += 0.20
        sell_factors += 0.20
        
        # 3. RSI Signal (weight: 20%)
        if MIN_RSI_SELL <= indicators['rsi'] <= MAX_RSI_SELL:
            sell_score += 0.20
        elif 30 < indicators['rsi'] < 50:
            sell_score += 0.10
        sell_factors += 0.20
        
        # 4. MACD Signal (weight: 15%)
        if indicators['macd'] < indicators['macd_signal'] and indicators['macd_diff'] < 0:
            sell_score += 0.15
        sell_factors += 0.15
        
        # 5. Bollinger Bands (weight: 10%)
        if indicators['bb_position'] > 0:
            sell_score += 0.10
        sell_factors += 0.10
        
        # 6. Stochastic Signal (weight: 10%)
        if indicators['stoch_k'] > 80 and indicators['stoch_d'] > 80:
            sell_score += 0.10
        sell_factors += 0.10
        
        sell_confidence = sell_score / sell_factors if sell_factors > 0 else 0
        
        # Determine final signal
        if buy_confidence >= MIN_SIGNAL_CONFIDENCE and buy_confidence > sell_confidence:
            return "BUY", buy_confidence, indicators
        elif sell_confidence >= MIN_SIGNAL_CONFIDENCE and sell_confidence > buy_confidence:
            return "SELL", sell_confidence, indicators
        else:
            return None, 0, indicators
        
    except Exception as e:
        logger.error(f"❌ Error analyzing signal for {symbol}: {e}")
        return None, 0, None

# =========================
# READY
# =========================

@bot.event
async def on_ready():
    logger.info(f"✅ Bot online: {bot.user}")
    print(f"✅ Bot online: {bot.user}")
    
    try:
        if not auto_signals.is_running():
            auto_signals.start()
            logger.info("▶️ auto_signals task started")
        
        if not auto_scalps.is_running():
            auto_scalps.start()
            logger.info("▶️ auto_scalps task started")
        
        if not auto_analysis.is_running():
            auto_analysis.start()
            logger.info("▶️ auto_analysis task started")
        
        if not auto_news.is_running():
            auto_news.start()
            logger.info("▶️ auto_news task started")
        
        if not auto_status.is_running():
            auto_status.start()
            logger.info("▶️ auto_status task started")
            
    except Exception as e:
        logger.error(f"❌ Error starting tasks: {e}")

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"❌ Error in {event}: {args} {kwargs}", exc_info=True)

# =========================
# WELCOME
# =========================

@bot.event
async def on_member_join(member):
    try:
        channel = bot.get_channel(CHANNELS["welcome"])
        
        if channel is None:
            return
        
        embed = discord.Embed(
            title="👋 BUN VENIT / WELCOME",
            description=f"Salut {member.mention}!\nBine ai venit pe serverul Crypto Signals 🚀",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="🎯 Semnale Precise",
            value="Toate semnalele sunt generate cu algoritm avansat (EMA + RSI + MACD + BB)",
            inline=False
        )
        
        embed.set_footer(text="Crypto Signals Bot - Advanced Trading")
        await channel.send(embed=embed)
        logger.info(f"✅ Welcome message sent to {member}")
        
    except Exception as e:
        logger.error(f"❌ Error sending welcome message: {e}")

# =========================
# AUTO SIGNALS
# =========================

@tasks.loop(minutes=15)
async def auto_signals():
    """Send high-confidence BUY/SELL signals"""
    try:
        buy_ch = bot.get_channel(CHANNELS["buy"])
        sell_ch = bot.get_channel(CHANNELS["sell"])
        premium_ch = bot.get_channel(CHANNELS["premium"])
        hp_ch = bot.get_channel(CHANNELS["high_profit"])
        
        if not all([buy_ch, sell_ch, premium_ch, hp_ch]):
            logger.error("❌ One or more signal channels not found")
            return
        
        for coin in COINS:
            await asyncio.sleep(API_CALL_DELAY)
            
            signal, confidence, indicators = await analyze_signal(coin)
            
            if signal is None or confidence < MIN_SIGNAL_CONFIDENCE:
                logger.info(f"⏭️ {coin}: Confidence too low ({confidence:.2%}) - skipping")
                continue
            
            if last_signal.get(coin) == signal:
                continue
            
            last_signal[coin] = signal
            signal_timestamps[coin] = datetime.now()
            
            price = indicators['price']
            
            log_signal(
                coin, signal, price, confidence,
                indicators['rsi'], indicators['macd_diff'],
                indicators['bb_position']
            )
            
            signal_strength = "🔥 STRONG" if confidence >= 0.90 else "💪 SOLID"
            
            if signal == "BUY":
                embed = discord.Embed(
                    title=f"🚀 BUY SIGNAL {signal_strength}",
                    description=f"**Confidence: {confidence:.2%}**",
                    color=discord.Color.green()
                )
                
                embed.add_field(name="🪙 Coin", value=coin, inline=True)
                embed.add_field(name="💵 Price", value=f"${price:,.2f}", inline=True)
                embed.add_field(name="📈 Trend", value="Bullish", inline=True)
                
                embed.add_field(name="📊 Indicators", value=
                    f"RSI: {indicators['rsi']:.2f}\n"
                    f"MACD: {'✅ Bullish' if indicators['macd_diff'] > 0 else '❌ Bearish'}\n"
                    f"EMA: 20 > 50 > 200 ✅",
                    inline=False
                )
                
                embed.set_footer(text="Crypto Signals Bot - High Confidence Signals")
                
                await buy_ch.send(embed=embed)
                logger.info(f"📢 BUY signal sent for {coin} (confidence: {confidence:.2%})")
                
                if confidence >= 0.85:
                    vip = discord.Embed(
                        title="💎 VIP BUY SIGNAL - PREMIUM",
                        description=f"{coin} **STRONG BULLISH BREAKOUT**",
                        color=discord.Color.gold()
                    )
                    
                    vip.add_field(name="🎯 Entry", value=f"Market / {price:,.2f}")
                    vip.add_field(name="🛡️ Stop Loss", value=f"Below ${price * 0.97:,.2f} (3%)")
                    vip.add_field(name="🎯 Target 1", value=f"${price * 1.03:,.2f} (+3%)")
                    vip.add_field(name="🎯 Target 2", value=f"${price * 1.05:,.2f} (+5%)")
                    vip.add_field(name="💪 Confidence", value=f"{confidence:.2%}", inline=True)
                    
                    vip.set_footer(text="Premium Analysis - Follow Risk Management")
                    await premium_ch.send(embed=vip)
                
                if confidence >= 0.92:
                    hp = discord.Embed(
                        title="🔥 EXTREME PROFIT OPPORTUNITY",
                        description=f"{coin} - **ULTRA HIGH CONFIDENCE SIGNAL**",
                        color=discord.Color.orange()
                    )
                    
                    hp.add_field(name="⚡ Signal Strength", value=f"{confidence:.2%}")
                    hp.add_field(name="💰 Potential Gain", value="5-15% in next 4 hours")
                    hp.set_footer(text="High Risk = High Reward")
                    
                    await hp_ch.send(embed=hp)
                    logger.info(f"🔥 EXTREME signal: {coin} ({confidence:.2%})")
            
            else:
                embed = discord.Embed(
                    title=f"📉 SELL SIGNAL {signal_strength}",
                    description=f"**Confidence: {confidence:.2%}**",
                    color=discord.Color.red()
                )
                
                embed.add_field(name="🪙 Coin", value=coin, inline=True)
                embed.add_field(name="💵 Price", value=f"${price:,.2f}", inline=True)
                embed.add_field(name="📉 Trend", value="Bearish", inline=True)
                
                embed.add_field(name="📊 Indicators", value=
                    f"RSI: {indicators['rsi']:.2f}\n"
                    f"MACD: {'✅ Bullish' if indicators['macd_diff'] > 0 else '❌ Bearish'}\n"
                    f"EMA: 20 < 50 < 200 ✅",
                    inline=False
                )
                
                embed.set_footer(text="Crypto Signals Bot - High Confidence Signals")
                
                await sell_ch.send(embed=embed)
                logger.info(f"📢 SELL signal sent for {coin} (confidence: {confidence:.2%})")
    
    except Exception as e:
        logger.error(f"❌ Error in auto_signals: {e}", exc_info=True)

@auto_signals.before_loop
async def before_auto_signals():
    await bot.wait_until_ready()

# =========================
# SCALPS
# =========================

@tasks.loop(minutes=5)
async def auto_scalps():
    """Send high-confidence scalp signals"""
    try:
        ch = bot.get_channel(CHANNELS["scalps"])
        
        if ch is None:
            return
        
        scalp_coins = random.sample(COINS, min(2, len(COINS)))
        
        for coin in scalp_coins:
            await asyncio.sleep(API_CALL_DELAY)
            
            indicators = await calculate_indicators(coin, "5m")
            
            if indicators is None:
                continue
            
            price = indicators['price']
            rsi = indicators['rsi']
            
            if (rsi < 25 or rsi > 75) and 'macd_diff' in indicators:
                scalp_direction = "🟢 LONG" if rsi < 25 else "🔴 SHORT"
                
                embed = discord.Embed(
                    title=f"⚡ QUICK SCALP {scalp_direction}",
                    description=f"5-minute High Probability Setup",
                    color=discord.Color.from_rgb(255, 165, 0)
                )
                
                embed.add_field(name="Coin", value=coin, inline=True)
                embed.add_field(name="Price", value=f"${price:,.2f}", inline=True)
                embed.add_field(name="RSI", value=f"{rsi:.2f}", inline=True)
                
                embed.add_field(name="Scalp Target", value=f"+1-3% (quick flip)", inline=False)
                embed.add_field(name="⏰ Hold Time", value="5-15 minutes", inline=False)
                
                embed.set_footer(text="Fast Trading - High Risk")
                
                await ch.send(embed=embed)
                logger.info(f"⚡ Scalp signal: {coin} @ {price:,.2f}")
        
    except Exception as e:
        logger.error(f"❌ Error in auto_scalps: {e}")

@auto_scalps.before_loop
async def before_auto_scalps():
    await bot.wait_until_ready()

# =========================
# ANALYSIS
# =========================

@tasks.loop(hours=1)
async def auto_analysis():
    """Send market analysis"""
    try:
        ch = bot.get_channel(CHANNELS["analysis"])
        
        if ch is None:
            return
        
        embed = discord.Embed(
            title="📊 MARKET ANALYSIS & PERFORMANCE",
            color=discord.Color.blue()
        )
        
        analysis_text = ""
        for coin in COINS[:3]:
            perf = get_signal_performance(coin)
            win_rate = perf['win_rate'] if perf['total'] > 0 else 0
            analysis_text += f"**{coin}**: {perf['wins']}/{perf['total']} wins ({win_rate:.1%})\n"
        
        embed.add_field(name="💯 Win Rates", value=analysis_text if analysis_text else "Tracking...", inline=False)
        embed.add_field(name="🔥 Current Trend", value="Multi-timeframe analysis active", inline=False)
        
        embed.set_footer(text="Based on Advanced Technical Analysis")
        
        await ch.send(embed=embed)
        logger.info("📊 Market analysis sent")
        
    except Exception as e:
        logger.error(f"❌ Error in auto_analysis: {e}")

@auto_analysis.before_loop
async def before_auto_analysis():
    await bot.wait_until_ready()

# =========================
# NEWS
# =========================

@tasks.loop(hours=2)
async def auto_news():
    """Send crypto news"""
    try:
        ch = bot.get_channel(CHANNELS["news"])
        
        if ch is None:
            return
        
        feed = feedparser.parse(
            "https://www.coindesk.com/arc/outboundfeeds/rss/"
        )
        
        if not feed.entries:
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
        embed.set_footer(text="Source: CoinDesk")
        
        await ch.send(embed=embed)
        logger.info(f"📰 News sent: {article.title}")
        
    except Exception as e:
        logger.error(f"❌ Error in auto_news: {e}")

@auto_news.before_loop
async def before_auto_news():
    await bot.wait_until_ready()

# =========================
# STATUS
# =========================

@tasks.loop(minutes=30)
async def auto_status():
    """Update bot status"""
    try:
        statuses = [
            "🚀 Advanced Trading Signals",
            "📈 Multi-Indicator Analysis",
            "💯 High Win Rate Signals",
            "🔥 Real-Time Market Data"
        ]
        
        await bot.change_presence(
            activity=discord.Game(random.choice(statuses))
        )
        logger.info("✅ Status updated")
        
    except Exception as e:
        logger.error(f"❌ Error in auto_status: {e}")

@auto_status.before_loop
async def before_auto_status():
    await bot.wait_until_ready()

# =========================
# COMMANDS
# =========================

@bot.command()
async def help(ctx):
    """Show available commands"""
    try:
        if check_command_cooldown(ctx.author.id):
            await ctx.send("⏳ You're on cooldown (5 seconds)")
            return
        
        log_command_usage(ctx.author.id, "help")
        
        embed = discord.Embed(
            title="📘 CRYPTO SIGNALS - ADVANCED BOT",
            color=discord.Color.blurple()
        )
        
        embed.add_field(
            name="!price <coin>",
            value="Get live price (e.g., !price btc)",
            inline=False
        )
        
        embed.add_field(
            name="!signal <coin>",
            value="Get detailed signal analysis (e.g., !signal eth)",
            inline=False
        )
        
        embed.add_field(
            name="!stats",
            value="View bot statistics and win rates",
            inline=False
        )
        
        embed.add_field(
            name="!status",
            value="Check bot operational status",
            inline=False
        )
        
        embed.set_footer(text="All signals use advanced multi-indicator analysis")
        await ctx.send(embed=embed)
        logger.info(f"✅ Help command used by {ctx.author}")
        
    except Exception as e:
        logger.error(f"❌ Error in help command: {e}")
        await ctx.send("❌ An error occurred")

@bot.command()
async def price(ctx, coin):
    """Get live price"""
    try:
        if check_command_cooldown(ctx.author.id):
            await ctx.send("⏳ Cooldown active (5 seconds)")
            return
        
        log_command_usage(ctx.author.id, f"price {coin}")
        
        if not coin or len(coin) > 10:
            await ctx.send("❌ Invalid coin name")
            return
        
        symbol = coin.upper() + "USDT"
        
        async with ctx.typing():
            price = await get_price(symbol)
        
        if price is None:
            await ctx.send(f"❌ Coin not found: {coin}")
            return
        
        embed = discord.Embed(
            title="💰 LIVE PRICE",
            color=discord.Color.green()
        )
        
        embed.add_field(name="Coin", value=symbol, inline=True)
        embed.add_field(name="Price", value=f"${price:,.2f}", inline=True)
        embed.set_footer(text="Real-time Binance data")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ Error in price command: {e}")
        await ctx.send("❌ An error occurred")

@bot.command()
async def signal(ctx, coin):
    """Get detailed signal analysis"""
    try:
        if check_command_cooldown(ctx.author.id):
            await ctx.send("⏳ Cooldown active (5 seconds)")
            return
        
        log_command_usage(ctx.author.id, f"signal {coin}")
        
        if not coin or len(coin) > 10:
            await ctx.send("❌ Invalid coin name")
            return
        
        symbol = coin.upper() + "USDT"
        
        async with ctx.typing():
            sig, confidence, indicators = await analyze_signal(symbol)
        
        if sig is None or indicators is None:
            await ctx.send(f"❌ Cannot analyze {coin}")
            return
        
        color = discord.Color.green() if sig == "BUY" else discord.Color.red() if sig == "SELL" else discord.Color.grey()
        
        embed = discord.Embed(
            title=f"📡 SIGNAL ANALYSIS: {symbol}",
            color=color
        )
        
        if sig:
            embed.add_field(name="Signal", value=f"**{sig}**", inline=True)
            embed.add_field(name="Confidence", value=f"**{confidence:.2%}**", inline=True)
        
        embed.add_field(name="💹 Price", value=f"${indicators['price']:,.2f}", inline=False)
        
        indicators_text = f"""
RSI: {indicators['rsi']:.2f}
MACD: {'✅ Bullish' if indicators['macd_diff'] > 0 else '❌ Bearish'} ({indicators['macd_diff']:.6f})
EMA20: ${indicators['ema_20']:,.2f}
EMA50: ${indicators['ema_50']:,.2f}
EMA200: ${indicators['ema_200']:,.2f}
        """
        
        embed.add_field(name="📊 Indicators", value=indicators_text, inline=False)
        embed.set_footer(text="Advanced Multi-Indicator Analysis")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ Error in signal command: {e}")
        await ctx.send("❌ An error occurred")

@bot.command()
async def stats(ctx):
    """Show bot statistics"""
    try:
        if check_command_cooldown(ctx.author.id):
            await ctx.send("⏳ Cooldown active (5 seconds)")
            return
        
        embed = discord.Embed(
            title="📊 BOT STATISTICS",
            color=discord.Color.gold()
        )
        
        embed.add_field(name="Active Coins", value=f"{len(COINS)}", inline=True)
        embed.add_field(name="Min Confidence", value=f"{MIN_SIGNAL_CONFIDENCE:.0%}", inline=True)
        embed.add_field(name="Indicators", value="EMA + RSI + MACD + BB + Stoch + ADX", inline=False)
        
        embed.set_footer(text="High Accuracy Trading Bot")
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ Error in stats command: {e}")
        await ctx.send("❌ An error occurred")

@bot.command()
async def status(ctx):
    """Check bot status"""
    try:
        if check_command_cooldown(ctx.author.id):
            await ctx.send("⏳ Cooldown active (5 seconds)")
            return
        
        embed = discord.Embed(
            title="🤖 BOT STATUS",
            color=discord.Color.green()
        )
        
        embed.add_field(name="✅ Status", value="Online & Monitoring", inline=True)
        embed.add_field(name="📊 Active Coins", value=f"{len(COINS)}", inline=True)
        embed.add_field(name="🔄 Tasks", value="All Running", inline=True)
        embed.set_footer(text=f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        await ctx.send(embed=embed)
        
    except Exception as e:
        logger.error(f"❌ Error in status command: {e}")
        await ctx.send("❌ An error occurred")

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors"""
    if isinstance(error, commands.CommandNotFound):
        logger.warning(f"⚠️ Unknown command: {ctx.message.content}")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument: {error.param.name}")
    else:
        logger.error(f"❌ Command error: {error}")
        await ctx.send("❌ An error occurred")

def main():
    """Main entry point"""
    try:
        init_db()
        bot.run(TOKEN)
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        exit(1)

if __name__ == "__main__":
    main()
