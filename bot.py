import os
import asyncio
import json
import logging
import requests
import time
import collections
import numpy as np
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-REBORN")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15,
    "POLL_INTERVAL": 1, 
    "COOLDOWN": 15,     
    "FEE_PERCENT": Decimal("0.001"),   
    "SLIPPAGE_BPS": Decimal("0.0005"),
    "RSI_THRESHOLD": 45, # Lowered for higher trade frequency
    "TARGET_PROB": Decimal("0.90") 
}

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.price_history = collections.deque(maxlen=20) 
        self.high_history = collections.deque(maxlen=20)
        self.low_history = collections.deque(maxlen=20)
        
        self.active_id = None
        self.is_trading = False
        self.demo_balance = Decimal("200.00") 
        self.trade_count = 0
        self.start_time = time.time()
        
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        """Dynamic Market Hunter: Finds the newest active BTC market"""
        try:
            url = "https://gamma-api.polymarket.com/markets?active=true&limit=10&query=BTC&order=createdAt&ascending=false"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for market in data:
                    if not market.get('closed') and market.get('clobTokenIds'):
                        token_ids = json.loads(market['clobTokenIds'])
                        logger.info(f"🎯 Market Found: {market['question']}")
                        return token_ids[0] 
            return None
        except Exception as e:
            logger.error(f"⚠️ Discovery Error: {e}")
            return None

    def calculate_indicators(self):
        """Calculates RSI and a simple RDI-style Trend Delta"""
        if len(self.price_history) < 14:
            return 50, 0
        
        prices = np.array(list(self.price_history), dtype=float)
        deltas = np.diff(prices)
        seed = deltas[:14]
        up = seed[seed >= 0].sum() / 14
        down = -seed[seed < 0].sum() / 14
        rs = up / (down if down != 0 else 0.001)
        rsi = 100. - (100. / (1. + rs))
        
        # RDI (Relative Directional Index) logic: Current vs 5-period Moving Average
        rdi = prices[-1] - np.mean(prices[-5:])
        return rsi, rdi

    async def start_binance_feed(self):
        try:
            client = await AsyncClient.create() 
            bm = BinanceSocketManager(client)
            ms = bm.symbol_ticker_socket('BTCUSDT')
            async with ms as tscm:
                logger.info("✅ Binance Feed Connected")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
                        p = Decimal(res['c'])
                        self.binance_price = p
                        self.price_history.append(p)
                        self.high_history.append(Decimal(res['h']))
                        self.low_history.append(Decimal(res['l']))
        except Exception as e:
            logger.error(f"Binance Error: {e}")
            await asyncio.sleep(5)

    async def heartbeat_logger(self):
        while True:
            if self.binance_price > 0:
                rsi, rdi = self.calculate_indicators()
                uptime = round((time.time() - self.start_time) / 60, 1)
                logger.info(f"💓 [UPTIME: {uptime}m] BTC: ${self.binance_price} | RSI: {round(rsi, 1)} | Balance: ${round(self.demo_balance, 2)}")
            await asyncio.sleep(15)

    async def check_arbitrage_loop(self):
        logger.info("🔍 Monitoring Binance vs Polymarket...")
        while True:
            if not self.active_id:
                self.active_id = self.fetch_active_token()
                if not self.active_id:
                    await asyncio.sleep(5)
                    continue

            try:
                # 1. Get Poly Price
                book = self.poly_client.get_order_book(self.active_id)
                if not hasattr(book, 'asks') or not book.asks:
                    continue
                
                poly_price = Decimal(str(book.asks[0].price))
                rsi, rdi = self.calculate_indicators()

                # 2. Strategy Logic: Binance Moving Up + Poly Price Lagging
                if rsi > CONFIG["RSI_THRESHOLD"] and rdi > 0:
                    if poly_price < CONFIG["TARGET_PROB"] and not self.is_trading:
                        await self.execute_realistic_demo(poly_price, rsi)

            except Exception as e:
                # FIX: Catch the 404 and reset ID to find a new market
                if "404" in str(e) or "No orderbook" in str(e):
                    logger.warning("🔄 Current market expired. Resetting...")
                    self.active_id = None
                else:
                    logger.error(f"Loop Error: {e}")
                await asyncio.sleep(2)
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_realistic_demo(self, price, current_rsi):
        self.is_trading = True
        self.trade_count += 1
        
        fee_cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * CONFIG["FEE_PERCENT"]
        total_deduction = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) + fee_cost
        self.demo_balance -= total_deduction
        
        logger.info("---------- ⚡ TRADE EXECUTED (DEMO) ----------")
        logger.info(f"RSI: {round(current_rsi, 2)} | Poly Price: {price}")
        logger.info(f"Deducted: ${total_deduction} | Remaining: ${round(self.demo_balance, 2)}")
        logger.info("---------------------------------------------")
        
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def run(self):
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop(),
            self.heartbeat_logger()
        )

if __name__ == "__main__":
    try:
        asyncio.run(ArbBot().run())
    except KeyboardInterrupt:
        pass
    except Exception as global_e:
        logger.critical(f"FATAL CRASH: {global_e}")
