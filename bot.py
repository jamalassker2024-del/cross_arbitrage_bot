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

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logger = logging.getLogger("OctoArb-PRO-DEMO")

# --- REALISM CONFIG ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,
    "POLL_INTERVAL": 2,
    "COOLDOWN": 60,
    # --- REALISM FACTORS ---
    "FEE_PERCENT": Decimal("0.015"),   # 1.5% Polymarket Fee simulation
    "SLIPPAGE_BPS": Decimal("0.005"),  # 0.5% price penalty (getting a worse fill)
}

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.last_binance_price = Decimal("0")
        self.active_id = None
        self.is_trading = False
        self.demo_balance = Decimal("200.00") 
        
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
            headers = {'User-Agent': 'Mozilla/5.0'}
            url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
            resp = requests.get(url, headers=headers, timeout=10).json()
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
                logger.info("✅ Binance Live (High-Fidelity Demo)")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
                        self.last_binance_price = self.binance_price
                        self.binance_price = Decimal(res['c'])
        except Exception as e:
            logger.error(f"Binance Error: {e}")
            await asyncio.sleep(5)

    async def check_arbitrage_loop(self):
        logger.info(f"🔍 Monitoring... Balance: ${self.demo_balance}")
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
                    
                    # TRIGGER: Price must be rising AND "cheap"
                    if self.binance_price > self.last_binance_price and raw_price < Decimal("0.90"):
                        if not self.is_trading:
                            await self.execute_realistic_demo(raw_price)

            except Exception as e:
                if "404" in str(e): self.active_id = None
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_realistic_demo(self, price):
        """Simulates a trade with Fees and Slippage applied"""
        self.is_trading = True
        
        # 1. APPLY SLIPPAGE (Getting a worse price than what we saw)
        # Real Price = Price + (Price * 0.005)
        executed_price = price * (1 + CONFIG["SLIPPAGE_BPS"])
        
        # 2. CALCULATE FEES
        # Total Cost = Amount + (Amount * 0.015)
        fee_cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * CONFIG["FEE_PERCENT"]
        total_deduction = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) + fee_cost
        
        # 3. UPDATE VIRTUAL ACCOUNT
        shares = float(CONFIG["TRADE_AMOUNT_USDC"]) / float(executed_price)
        self.demo_balance -= total_deduction
        
        logger.info("---------- 🧪 REALISTIC TRADE ----------")
        logger.info(f"Target Price: ${price} | Executed at: ${round(executed_price, 4)}")
        logger.info(f"Fee Paid: -${round(fee_cost, 4)}")
        logger.info(f"Shares Bought: {round(shares, 2)}")
        logger.info(f"NEW BALANCE: ${round(self.demo_balance, 2)}")
        logger.info("----------------------------------------")
        
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def run(self):
        await asyncio.gather(self.start_binance_feed(), self.check_arbitrage_loop())

if __name__ == "__main__":
    asyncio.run(ArbBot().run())
