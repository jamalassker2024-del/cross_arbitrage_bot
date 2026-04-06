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
from py_clob_client.clob_types import OrderArgs

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logger = logging.getLogger("OctoArb-DEMO")

# --- CONFIG ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,       # Virtual trade size
    "POLL_INTERVAL": 2,            
    "COOLDOWN": 60,                # Shorter cooldown for Demo mode
}

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.last_binance_price = Decimal("0")
        self.active_id = None
        self.is_trading = False
        self.demo_balance = Decimal("200.00") # Starting virtual balance
        
        # We keep the client initialized to fetch real order book data
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, # Dummy key for demo if env is empty
            chain_id=POLYGON,
            signature_type=1
        )

    def get_current_btc_token_id(self):
        """Fetches real active market from Gamma API"""
        try:
            ts = int(time.time() // 900) * 900
            slug_options = [f"btc-updown-15m-{ts}", f"bitcoin-price-above-below-15m-{ts}"]
            headers = {'User-Agent': 'Mozilla/5.0'}
            
            for slug in slug_options:
                url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
                resp = requests.get(url, headers=headers, timeout=10).json()
                if resp and len(resp) > 0:
                    raw_ids = resp[0].get('clobTokenIds')
                    if raw_ids:
                        token_ids = json.loads(raw_ids)
                        return token_ids[0]
            return None
        except Exception as e:
            logger.error(f"Market Hunt Error: {e}")
            return None

    async def start_binance_feed(self):
        """Streams real BTC price"""
        try:
            client = await AsyncClient.create(tld='us') 
            bm = BinanceSocketManager(client)
            ms = bm.symbol_ticker_socket('BTCUSDT')
            async with ms as tscm:
                logger.info("✅ Binance Feed Live (Demo Mode)")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
                        self.last_binance_price = self.binance_price
                        self.binance_price = Decimal(res['c'])
        except Exception as e:
            logger.error(f"Binance Error: {e}")
            await asyncio.sleep(5)

    async def check_arbitrage_loop(self):
        """Monitors for opportunities and simulates trades"""
        logger.info(f"🔍 Monitoring... (Starting Demo Balance: ${self.demo_balance})")
        while True:
            if not self.active_id:
                self.active_id = self.get_current_btc_token_id()
                if not self.active_id:
                    await asyncio.sleep(10)
                    continue

            if self.binance_price == 0:
                await asyncio.sleep(1)
                continue

            try:
                book = self.poly_client.get_order_book(self.active_id)
                if book and book.asks:
                    poly_ask = Decimal(str(book.asks[0].price))
                    
                    if int(time.time()) % 10 == 0:
                        logger.info(f"DEMO: BTC ${self.binance_price} | Poly Yes ${poly_ask}")

                    # --- TRIGGER LOGIC ---
                    is_pumping = (self.binance_price > self.last_binance_price)
                    
                    if is_pumping and poly_ask < Decimal("0.92") and not self.is_trading:
                        # Simulate the trade without calling the API
                        await self.execute_demo_trade(poly_ask)

            except Exception as e:
                if "404" in str(e):
                    self.active_id = None
                else:
                    logger.error(f"Orderbook Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_demo_trade(self, price):
        """Simulates a trade and updates demo balance"""
        self.is_trading = True
        logger.info("--------------------------------------------------")
        logger.info(f"🧪 DEMO SIGNAL: Buying YES at ${price}")
        
        # Calculate shares (Virtual)
        shares = float(CONFIG["TRADE_AMOUNT_USDC"]) / float(price)
        self.demo_balance -= Decimal(str(CONFIG["TRADE_AMOUNT_USDC"]))
        
        logger.info(f"🛒 PURCHASE: {round(shares, 2)} shares for ${CONFIG['TRADE_AMOUNT_USDC']}")
        logger.info(f"💰 CURRENT DEMO BALANCE: ${self.demo_balance}")
        logger.info("--------------------------------------------------")
        
        # Short cooldown for demo purposes
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def run(self):
        logger.info("🚀 Bot Started - DEMO / PAPER TRADING MODE")
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop()
        )

if __name__ == "__main__":
    bot = ArbBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Stopped.")
