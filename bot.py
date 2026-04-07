import asyncio
import logging
import requests
import time
import collections
from decimal import Decimal

# --- DEMO CONFIGURATION ---
CONFIG = {
    "TRADE_SIZE_USD": 20.00,      # Simulated position size
    "MIN_SPREAD_PERCENT": 0.35,   # Trigger if Poly is 0.35% cheaper than Binance
    "SENTIMENT_THRESHOLD": 0.52,  # Trigger if News is slightly Bullish
    "POLL_INTERVAL": 3,           # Check every 3 seconds for Demo
    "SYMBOL": "BTCUSDT"
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-Demo")

class OctoArbDemo:
    def __init__(self):
        self.binance_url = f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={CONFIG['SYMBOL']}"
        self.sentiment_score = 0.5
        self.demo_balance = Decimal("1000.00") # Start with $1000 Fake USD
        self.active_trades = 0
        self.total_profit = Decimal("0.00")

    def fetch_sentiment(self):
        """Public news scraper simulation"""
        try:
            # Using CryptoPanic public aggregate for demo
            res = requests.get("https://cryptopanic.com/api/v1/posts/?filter=hot", timeout=5)
            data = res.json()
            votes = data['results'][0]['votes']
            pos = votes.get('positive', 0) + votes.get('liked', 0) + 1
            neg = votes.get('negative', 0) + votes.get('disliked', 0) + 1
            self.sentiment_score = pos / (pos + neg)
        except:
            self.sentiment_score = 0.5 # Default Neutral

    def check_arbitrage(self, binance_price):
        """
        CROSS-ARBITRAGE LOGIC:
        In a real scenario, we'd pull the actual Polymarket Order Book.
        For this demo, we simulate a 'Lagging' Polymarket price.
        """
        # Simulate Polymarket being slightly slower to react to Binance
        poly_price = binance_price * 0.996 # 0.4% simulated gap
        spread = ((binance_price - poly_price) / binance_price) * 100

        # DECISION ENGINE
        if spread >= CONFIG["MIN_SPREAD_PERCENT"] and self.sentiment_score >= CONFIG["SENTIMENT_THRESHOLD"]:
            self.execute_demo_trade(binance_price, poly_price, spread)

    def execute_demo_trade(self, b_price, p_price, spread):
        """Logs a simulated Delta-Neutral trade"""
        self.active_trades += 1
        # In a real Arb, profit is roughly (Spread - Fees)
        fees = Decimal("0.1") # 0.1% for Binance + Polygon gas
        simulated_gain = (Decimal(str(spread)) - fees) / 100 * Decimal(str(CONFIG["TRADE_SIZE_USD"]))
        
        self.total_profit += simulated_gain
        self.demo_balance += simulated_gain

        logger.info("🚀 [DEMO TRADE EXECUTED]")
        logger.info(f"   | Reason: Spread {round(spread, 3)}% & Sentiment {round(self.sentiment_score, 2)}")
        logger.info(f"   | Action: Short Binance @ ${b_price} / Long Poly @ ${round(p_price, 2)}")
        logger.info(f"   | Est. Profit: +${round(simulated_gain, 4)}")
        logger.info(f"   | New Demo Balance: ${round(self.demo_balance, 2)}")

    async def run(self):
        logger.info("--- OctoArb V5.3 PURE DEMO MODE ACTIVE ---")
        logger.info(f"Initial Demo Balance: ${self.demo_balance}")
        
        while True:
            try:
                # 1. Get Public Price
                r = requests.get(self.binance_url, timeout=2)
                price = float(r.json()['price'])
                
                # 2. Update Sentiment
                self.fetch_sentiment()

                # 3. Arbitrage Check
                self.check_arbitrage(price)

                # 4. Heartbeat Log
                logger.info(f"LIVE | BTC: ${price} | Sent: {round(self.sentiment_score, 2)} | Trades: {self.active_trades}")

            except Exception as e:
                logger.error(f"Demo Loop Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

if __name__ == "__main__":
    bot = OctoArbDemo()
    asyncio.run(bot.run())
