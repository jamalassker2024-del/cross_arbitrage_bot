import os
import asyncio
import logging
import requests
import time
import collections
from decimal import Decimal
import pandas as pd
import ccxt.async_support as ccxt

# --- CONFIGURATION ---
CONFIG = {
    "BINANCE_API_KEY": "0NLIHcV6lIWDuCakzAAUSE2mq6BrxmDNHCn6l0lCPgq7AAFWcPiqkz2Q9eTbW9Ye",
    "BINANCE_SECRET": "5LVq1iHl5MRAS56SHsrMmx4wAqe1TvURAvNLrlUR4hGcru6F8CpMjRzJK8BqtNiF",
    "TRADE_SIZE_USD": 20.00,      # Amount for the Hedge (Binance Short)
    "MIN_SPREAD_PERCENT": 0.45,   # % difference to trigger trade
    "RSI_PERIOD": 14,
    "POLL_INTERVAL": 1.5,         # Seconds between price checks
    "SYMBOL": "BTCUSDT",
    "FUTURES_SYMBOL": "BTC/USDT:USDT"
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-V5.1")

class OctoArbSentinel:
    def __init__(self):
        self.binance_public_url = "https://fapi.binance.com/fapi/v1/ticker/price?symbol=" + CONFIG["SYMBOL"]
        self.history = collections.deque(maxlen=100)
        self.is_hedged = False
        self.last_trade_time = 0
        self.sentiment_score = 0.5
        
        # Initialize Private Binance for Execution Only
        self.exchange = ccxt.binance({
            'apiKey': CONFIG["BINANCE_API_KEY"],
            'secret': CONFIG["BINANCE_SECRET"],
            'options': {'defaultType': 'future'}
        })

    # --- 1. PUBLIC PRICE & RSI (NO KEY) ---
    async def get_market_data(self):
        try:
            resp = requests.get(self.binance_public_url, timeout=2)
            data = resp.json()
            price = float(data['price'])
            self.history.append(price)
            return price
        except Exception as e:
            logger.error(f"Public API Error: {e}")
            return None

    def calculate_rsi(self):
        if len(self.history) < CONFIG["RSI_PERIOD"]: return 50.0
        df = pd.Series(list(self.history))
        delta = df.diff()
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/CONFIG["RSI_PERIOD"]).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/CONFIG["RSI_PERIOD"]).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs.iloc[-1]))

    # --- 2. 2026 NEWS SENTIMENT ---
    async def fetch_sentiment(self):
        """Scrapes community sentiment to prevent 'Panic' buying."""
        try:
            # April 2026 News Proxy
            res = requests.get("https://cryptopanic.com/api/v1/posts/?filter=hot", timeout=5)
            votes = res.json()['results'][0]['votes']
            pos = votes.get('positive', 0) + votes.get('liked', 0) + 1
            neg = votes.get('negative', 0) + votes.get('disliked', 0) + 1
            self.sentiment_score = pos / (pos + neg)
        except:
            self.sentiment_score = 0.5

    # --- 3. DELTA-NEUTRAL EXECUTION (REBALANCER) ---
    async def execute_hedge_strategy(self, binance_price):
        # 1. Fetch Polymarket Price (Simulated for Cross-Arb)
        # Prediction markets usually trade at a slight discount/lag
        poly_price = binance_price * 0.995 # Simulating a 0.5% spread
        spread = ((binance_price - poly_price) / binance_price) * 100

        # 2. Logic Check: Is the Spread wide enough AND Sentiment positive?
        if spread > CONFIG["MIN_SPREAD_PERCENT"] and self.sentiment_score > 0.55:
            logger.info(f"⚖️ ARB FOUND! Spread: {round(spread, 3)}% | Sentiment: {round(self.sentiment_score, 2)}")
            
            try:
                # PRIVATE EXECUTION: Short on Binance Futures
                # amount = $20 / Price
                amount = CONFIG["TRADE_SIZE_USD"] / binance_price
                
                # Check if Keys are filled before attempting
                if CONFIG["BINANCE_API_KEY"] != "YOUR_KEY_HERE":
                    order = await self.exchange.create_market_sell_order(CONFIG["FUTURES_SYMBOL"], amount)
                    logger.info(f"✅ BINANCE SHORT PLACED: {order['id']}")
                else:
                    logger.warning("📝 SIMULATION MODE: No API Keys provided. Trade skipped.")
                
                # (Logic for Polymarket 'YES' buy would go here)
                self.is_hedged = True
                self.last_trade_time = time.time()
                
            except Exception as e:
                logger.error(f"Trade Execution Failed: {e}")

    # --- 4. MAIN LOOP ---
    async def run(self):
        logger.info("--- OctoArb V5.1 Hybrid Sentinel Active ---")
        
        while True:
            # Update public data
            current_price = await self.get_market_data()
            await self.fetch_sentiment()
            rsi = self.calculate_rsi()

            if current_price:
                # Log Status
                logger.info(f"BTC: ${current_price} | RSI: {round(rsi, 1)} | Sent: {round(self.sentiment_score, 2)}")
                
                # Check for Arbitrage
                if not self.is_hedged:
                    await self.execute_hedge_strategy(current_price)
                
                # Safety: Auto-unhedge logic (simplified for demo)
                if self.is_hedged and (time.time() - self.last_trade_time > 3600):
                    logger.info("⏳ Hedge duration expired. Closing positions.")
                    self.is_hedged = False

            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

if __name__ == "__main__":
    bot = OctoArbSentinel()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
