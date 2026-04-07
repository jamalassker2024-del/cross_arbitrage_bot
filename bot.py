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
logger = logging.getLogger("OctoArb-REBORN")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15,
    "POLL_INTERVAL": 1, # Faster polling
    "COOLDOWN": 10,     # Reduced cooldown for demo testing
    "FEE_PERCENT": Decimal("0.001"),   
    "SLIPPAGE_BPS": Decimal("0.0005"),
    "TARGET_PROBABILITY": Decimal("0.95") # High threshold to ensure it triggers on 'cheap' Yes shares
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
        
        # Initialize client (using dummy key for demo/simulation)
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        """Finds any active BTC price prediction market"""
        try:
            # Querying for active BTC markets generally rather than guessing the exact 15m slug
            url = "https://gamma-api.polymarket.com/markets?active=true&limit=5&query=BTC"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for market in data:
                    if 'clobTokenIds' in market and market['clobTokenIds']:
                        token_ids = json.loads(market['clobTokenIds'])
                        logger.info(f"🎯 Market Found: {market['question']}")
                        return token_ids[0] # Return the first outcome (usually 'Yes')
            return None
        except Exception as e:
            logger.error(f"⚠️ Market Discovery Error: {e}")
            return None

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
                        self.last_binance_price = self.binance_price
                        self.binance_price = Decimal(res['c'])
        except Exception as e:
            logger.error(f"Binance Error: {e}")
            await asyncio.sleep(5)

    async def heartbeat_logger(self):
        while True:
            if self.binance_price > 0:
                uptime = round((time.time() - self.start_time) / 60, 1)
                status = "IDLE" if not self.is_trading else "COOLDOWN"
                logger.info(f"💓 [{status} | {uptime}m] BTC: ${self.binance_price} | Trades: {self.trade_count} | Balance: ${round(self.demo_balance, 2)}")
            await asyncio.sleep(10)

    async def check_arbitrage_loop(self):
        logger.info("🔍 Monitoring Markets...")
        while True:
            # Refresh Active ID if we don't have one
            if not self.active_id:
                self.active_id = self.fetch_active_token()
                if not self.active_id:
                    await asyncio.sleep(10)
                    continue

            try:
                # Get Orderbook from Polymarket CLOB
                book = self.poly_client.get_order_book(self.active_id)
                
                # Check if asks exist
                if hasattr(book, 'asks') and len(book.asks) > 0:
                    # Accessing the first ask price correctly based on py_clob_client structure
                    raw_price = Decimal(str(book.asks[0].price))
                    
                    # SIMPLIFIED EXECUTION LOGIC:
                    # If the BTC price is healthy and the Polymarket 'Yes' price is below our target
                    if self.binance_price > 0 and raw_price < CONFIG["TARGET_PROBABILITY"]:
                        if not self.is_trading and self.demo_balance > CONFIG["TRADE_AMOUNT_USDC"]:
                            await self.execute_realistic_demo(raw_price)
                else:
                    logger.warning("Empty orderbook - waiting for liquidity...")

            except Exception as e:
                logger.error(f"Loop Error: {e}")
                self.active_id = None # Reset to find a fresh market
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_realistic_demo(self, price):
        self.is_trading = True
        self.trade_count += 1
        
        # Calculate costs
        executed_price = price * (1 + CONFIG["SLIPPAGE_BPS"])
        fee_cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * CONFIG["FEE_PERCENT"]
        total_deduction = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) + fee_cost
        
        self.demo_balance -= total_deduction
        
        logger.info("---------- ⚡ TRADE EXECUTED (DEMO) ----------")
        logger.info(f"Target: BTC Up | Poly Price: {price}")
        logger.info(f"Deducted: ${total_deduction} (Inc. Fees)")
        logger.info(f"NEW BALANCE: ${round(self.demo_balance, 2)}")
        logger.info("---------------------------------------------")
        
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def run(self):
        # Start all tasks
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop(),
            self.heartbeat_logger()
        )

if __name__ == "__main__":
    try:
        bot = ArbBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as global_e:
        logger.critical(f"FATAL CRASH: {global_e}")
