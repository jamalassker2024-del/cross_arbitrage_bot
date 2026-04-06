import os
import asyncio
import json
import logging
import requests
import time
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# --- LOGGING ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("OctoArb-AGGRESSIVE")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15,
    "POLL_INTERVAL": 2,
    "COOLDOWN": 30,                 # Reduced cooldown for more action
    "FEE_PERCENT": Decimal("0.015"),   
    "SLIPPAGE_BPS": Decimal("0.005"),
    "TARGET_PROBABILITY": Decimal("0.72") # Will buy anything under 72 cents
}

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.last_binance_price = Decimal("0")
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

    def get_current_btc_token_id(self):
        try:
            ts = int(time.time() // 900) * 900
            slug = f"btc-updown-15m-{ts}"
            url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
            resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10).json()
            if resp and len(resp) > 0:
                return json.loads(resp[0].get('clobTokenIds'))[0]
            return None
        except: return None

    async def start_binance_feed(self):
        try:
            client = await AsyncClient.create(tld='us') 
            bm = BinanceSocketManager(client)
            ms = bm.symbol_ticker_socket('BTCUSDT')
            async with ms as tscm:
                logger.info("✅ Binance Feed Live")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
                        self.last_binance_price = self.binance_price
                        self.binance_price = Decimal(res['c'])
        except Exception as e:
            logger.error(f"Binance Error: {e}")
            await asyncio.sleep(5)

    async def heartbeat_logger(self):
        while True:
            if self.binance_price > 0:
                uptime = round((time.time() - self.start_time) / 60, 1)
                logger.info(f"💓 [UPTIME: {uptime}m] BTC: ${self.binance_price} | Trades: {self.trade_count} | Balance: ${round(self.demo_balance, 2)}")
            await asyncio.sleep(15) # Pulse every 15s

    async def check_arbitrage_loop(self):
        logger.info(f"🔍 Aggressive Mode Active (Target < {CONFIG['TARGET_PROBABILITY']})")
        while True:
            if not self.active_id:
                self.active_id = self.get_current_btc_token_id()
                if not self.active_id:
                    await asyncio.sleep(10)
                    continue

            try:
                book = self.poly_client.get_order_book(self.active_id)
                if book and book.asks:
                    raw_price = Decimal(str(book.asks[0].price))
                    
                    # LOOSENED RULES:
                    # 1. Any upward movement (even tiny)
                    # 2. Price is under 72 cents (approx 70% probability + room for slippage)
                    if self.binance_price >= self.last_binance_price and raw_price < CONFIG["TARGET_PROBABILITY"]:
                        if not self.is_trading:
                            await self.execute_realistic_demo(raw_price)

            except Exception as e:
                if "404" in str(e): self.active_id = None
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_realistic_demo(self, price):
        self.is_trading = True
        self.trade_count += 1
        
        executed_price = price * (1 + CONFIG["SLIPPAGE_BPS"])
        fee_cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * CONFIG["FEE_PERCENT"]
        total_deduction = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) + fee_cost
        
        self.demo_balance -= total_deduction
        
        logger.info("---------- ⚡ AGGRESSIVE TRADE ----------")
        logger.info(f"Entry: ${price} | Final: ${round(executed_price, 4)}")
        logger.info(f"Fee: -${round(fee_cost, 2)} | Result: PENDING")
        logger.info(f"NEW BALANCE: ${round(self.demo_balance, 2)}")
        logger.info("----------------------------------------")
        
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def run(self):
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop(),
            self.heartbeat_logger()
        )

if __name__ == "__main__":
    asyncio.run(ArbBot().run())
