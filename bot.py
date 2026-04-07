import os
import asyncio
import json
import logging
import requests
import time
import collections
import pandas as pd  # Added for professional-grade math
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-V3-Pro")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15.00,
    "POLL_INTERVAL": 1, 
    "COOLDOWN": 180,             # 3-minute breather
    "FEE_PERCENT": Decimal("0.001"),   
    "RSI_PERIOD": 21,            # Longer period = smoother signal
    "RSI_BUY_THRESHOLD": 60,     # Higher bar for entry
    "RSI_EXIT_THRESHOLD": 30,    # Lower floor to prevent "Panic Exits"
    "MARKET_DURATION_SEC": 1200, 
    "MAX_HISTORY": 200,          # Larger pool for better math
    "CRYPTO_WATCHLIST": [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", 
        "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "SHIBUSDT", "DOTUSDT",
        "LINKUSDT", "TRXUSDT", "MATICUSDT", "LTCUSDT", "NEARUSDT",
        "UNIUSDT", "BCHUSDT", "PEPEUSDT", "APTUSDT", "STXUSDT"
    ]
}

class EliteArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.price_history = collections.deque(maxlen=CONFIG["MAX_HISTORY"])
        self.market_prices = {}
        self.active_id = None
        self.active_market_name = ""
        self.is_trading = False
        self.last_trade_time = 0
        self.demo_balance = Decimal("200.00")
        self.wins = 0
        self.losses = 0
        
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        try:
            # Query for High-Volume Prediction Markets
            url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20&sort=volume:desc"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data:
                    # Filter for BTC or high-activity crypto markets
                    if "BTC" in m.get('question', '').upper() and m.get('clobTokenIds'):
                        token_ids = json.loads(m['clobTokenIds'])
                        if token_ids:
                            logger.info(f"🎯 Target Acquired: {m.get('question')}")
                            return token_ids[0], m.get('question')
            return None, ""
        except Exception as e:
            logger.error(f"Error fetching token: {e}")
            return None, ""

    def calculate_rsi(self):
        """Uses EMA-based RSI (Wilder's Smoothing) for professional accuracy."""
        if len(self.price_history) < CONFIG["RSI_PERIOD"]: 
            return 50.0
        
        series = pd.Series(list(self.price_history))
        delta = series.diff()
        
        gain = (delta.where(delta > 0, 0)).ewm(alpha=1/CONFIG["RSI_PERIOD"], adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/CONFIG["RSI_PERIOD"], adjust=False).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return float(rsi.iloc[-1])

    async def start_binance_feed(self):
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        async with bm.multiplex_socket([f"{s.lower()}@ticker" for s in CONFIG["CRYPTO_WATCHLIST"]]) as mscm:
            while True:
                res = await mscm.recv()
                if res and 'data' in res:
                    data = res['data']
                    symbol = data['s']
                    price = Decimal(data['c'])
                    self.market_prices[symbol] = price
                    
                    if symbol == "BTCUSDT":
                        self.binance_price = price
                        self.price_history.append(float(price))

    async def trade_manager(self, entry_price, strike_at_trade, trade_id):
        start_t = time.time()
        logger.info(f"⏳ Trade #{trade_id} LIVE. Monitoring...")
        
        exit_reason = "EXPIRED"
        while (time.time() - start_t) < CONFIG["MARKET_DURATION_SEC"]:
            rsi = self.calculate_rsi()
            
            # Trailing Exit: Only bail if RSI is consistently dead (below 30)
            if rsi < CONFIG["RSI_EXIT_THRESHOLD"]:
                await asyncio.sleep(15) # Wait for confirmation
                if self.calculate_rsi() < CONFIG["RSI_EXIT_THRESHOLD"]:
                    exit_reason = "STOP_LOSS_RSI"
                    break
            
            await asyncio.sleep(10)

        # Simulation of Payouts
        shares = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) / entry_price
        if exit_reason == "STOP_LOSS_RSI":
            # Recover 70% of value (typical for a bad prediction market exit)
            payout = (Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * Decimal("0.70"))
            self.demo_balance += payout
            self.losses += 1
            logger.warning(f"🛑 EXIT: Trend Reversed. Recovered: ${round(payout, 2)}")
        elif self.binance_price > (strike_at_trade * Decimal("1.0001")): # Small buffer for profit
            payout = shares * Decimal("1.00")
            self.demo_balance += payout
            self.wins += 1
            logger.info(f"💰 WIN! Payout: ${round(payout, 2)}")
        else:
            self.losses += 1
            logger.info(f"📉 LOSS. Strike Price Not Met.")
        
        self.is_trading = False
        self.last_trade_time = time.time()

    async def check_arbitrage_loop(self):
        while True:
            # Check Cooldown
            if time.time() - self.last_trade_time < CONFIG["COOLDOWN"]:
                await asyncio.sleep(10)
                continue

            if not self.active_id:
                self.active_id, self.active_market_name = self.fetch_active_token()
                await asyncio.sleep(5)
                continue

            try:
                price_data = self.poly_client.get_price(self.active_id, side="BUY")
                poly_price = Decimal(str(price_data.get('price', '1.0')))
                rsi = self.calculate_rsi()

                # Rule: Don't buy if the market is already "expensive" (above 0.80)
                if poly_price > 0.80:
                    self.active_id = None
                    continue

                if rsi > CONFIG["RSI_BUY_THRESHOLD"] and not self.is_trading:
                    trade_id = self.wins + self.losses + 1
                    cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"]))
                    self.demo_balance -= (cost + (cost * CONFIG["FEE_PERCENT"]))
                    
                    self.is_trading = True
                    logger.info(f"🚀 EXECUTE #{trade_id} | RSI: {round(rsi,1)} | Entry: {poly_price}")
                    asyncio.create_task(self.trade_manager(poly_price, self.binance_price, trade_id))

            except Exception as e:
                logger.error(f"Loop Error: {e}")
                await asyncio.sleep(5)
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def heartbeat(self):
        while True:
            if self.binance_price > 0:
                total = self.wins + self.losses
                wr = (self.wins / total * 100) if total > 0 else 0
                rsi = self.calculate_rsi()
                logger.info(f"📊 BAL: ${round(self.demo_balance, 2)} | WR: {round(wr, 1)}% | RSI: {round(rsi, 1)} | BTC: ${self.binance_price}")
            await asyncio.sleep(20)

    async def run(self):
        logger.info("🔥 OctoArb V3 Online. Applying Exponential Smoothing...")
        await asyncio.gather(self.start_binance_feed(), self.check_arbitrage_loop(), self.heartbeat())

if __name__ == "__main__":
    asyncio.run(EliteArbBot().run())
