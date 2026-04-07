import os
import asyncio
import json
import logging
import requests
import time
import collections
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-Elite-V2")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15.00,
    "POLL_INTERVAL": 2,          # Increased to reduce noise
    "COOLDOWN": 300,             # 5-minute cooldown after a trade to prevent "churn"
    "FEE_PERCENT": Decimal("0.001"),   
    "RSI_PERIOD": 14,
    "RSI_BUY_THRESHOLD": 55,     # Require stronger momentum to enter
    "RSI_EXIT_THRESHOLD": 35,    # Lowered floor to give trades "breathing room"
    "MARKET_DURATION_SEC": 1800, # Extended duration to 30 mins
    "MAX_HISTORY": 100,          # More data points for a smoother RSI
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
        self.market_prices = {} # Top 20 Tracking
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
            url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&query=BTC&limit=10"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data:
                    if m.get('clobTokenIds') and not m.get('closed'):
                        token_ids = json.loads(m['clobTokenIds'])
                        if token_ids:
                            logger.info(f"🎯 Target Acquired: {m.get('question')}")
                            return token_ids[0], m.get('question')
            return None, ""
        except Exception as e:
            logger.error(f"Error fetching token: {e}")
            return None, ""

    def calculate_rsi(self):
        if len(self.price_history) < CONFIG["RSI_PERIOD"] + 1: 
            return 50.0
        
        prices = list(self.price_history)
        deltas = [float(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        
        # Use simple moving average for RSI smoothing
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [abs(d) if d < 0 else 0 for d in deltas]
        
        avg_gain = sum(gains[-CONFIG["RSI_PERIOD"]:]) / CONFIG["RSI_PERIOD"]
        avg_loss = sum(losses[-CONFIG["RSI_PERIOD"]:]) / CONFIG["RSI_PERIOD"]
        
        if avg_loss == 0: return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    async def start_binance_feed(self):
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        # Subscribe to top 20 + BTC
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
                        self.price_history.append(price)

    async def trade_manager(self, entry_price, strike_at_trade, trade_id):
        start_t = time.time()
        logger.info(f"⏳ Trade #{trade_id} LIVE. Monitoring...")
        
        exit_reason = "EXPIRED"
        while (time.time() - start_t) < CONFIG["MARKET_DURATION_SEC"]:
            current_rsi = self.calculate_rsi()
            
            # Implementation of "Breathing Room" - only exit if RSI stays low
            if current_rsi < CONFIG["RSI_EXIT_THRESHOLD"]:
                # Check again in 10 seconds to confirm it wasn't a flash dip
                await asyncio.sleep(10)
                if self.calculate_rsi() < CONFIG["RSI_EXIT_THRESHOLD"]:
                    exit_reason = "STOP_LOSS_RSI"
                    break
            
            await asyncio.sleep(10)

        # Settlement Logic
        shares = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) / entry_price
        if exit_reason == "STOP_LOSS_RSI":
            # Simulation of slippage and market exit cost
            payout = (Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * Decimal("0.85"))
            self.demo_balance += payout
            self.losses += 1
            logger.warning(f"🛑 EXIT: RSI Sustained Drop. Recovered: ${round(payout, 2)}")
        elif self.binance_price > strike_at_trade:
            payout = shares * Decimal("1.00")
            self.demo_balance += payout
            self.wins += 1
            logger.info(f"💰 WIN! Payout: ${round(payout, 2)}")
        else:
            self.losses += 1
            logger.info(f"📉 LOSS. BTC finished below strike.")
        
        self.is_trading = False
        self.last_trade_time = time.time()

    async def check_arbitrage_loop(self):
        while True:
            # Check for Cooldown
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

                # Don't buy into nearly-finished markets (Price > 0.85)
                if poly_price > 0.85:
                    self.active_id = None
                    continue

                if rsi > CONFIG["RSI_BUY_THRESHOLD"] and not self.is_trading:
                    trade_id = self.wins + self.losses + 1
                    cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"]))
                    self.demo_balance -= (cost + (cost * CONFIG["FEE_PERCENT"]))
                    
                    self.is_trading = True
                    logger.info(f"🚀 ENTERING TRADE #{trade_id} | RSI: {round(rsi,1)} | Poly: {poly_price} | BTC: {self.binance_price}")
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
                status = "📉 COOLING" if time.time() - self.last_trade_time < CONFIG["COOLDOWN"] else "🟢 READY"
                
                logger.info(f"--- STATUS: {status} ---")
                logger.info(f"BAL: ${round(self.demo_balance, 2)} | WR: {round(wr, 1)}% | RSI: {round(rsi, 1)}")
                logger.info(f"BTC: ${self.binance_price} | Active Market: {self.active_market_name[:40]}...")
            await asyncio.sleep(20)

    async def run(self):
        logger.info("🔥 Elite Bot Online. Tracking Top 20 and BTC...")
        await asyncio.gather(self.start_binance_feed(), self.check_arbitrage_loop(), self.heartbeat())

if __name__ == "__main__":
    asyncio.run(EliteArbBot().run())
