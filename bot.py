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
# Mute noisy library logs to see your trade signals clearly
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("py_clob_client").setLevel(logging.WARNING)
logger = logging.getLogger("OctoArb-LIVE")

# --- CONFIG ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,       # Amount per trade
    "POLL_INTERVAL": 2,            # Check Poly every 2s
    "COOLDOWN": 900,               # Wait 15m (one full cycle) after a trade
}

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.last_binance_price = Decimal("0")
        self.active_id = None
        self.is_trading = False
        
        # Initialize Polymarket Client
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY"),
            chain_id=POLYGON,
            signature_type=1
        )

    def get_current_btc_token_id(self):
        """Fetches active market from Gamma API"""
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
                        new_id = token_ids[0] # YES Token
                        logger.info(f"🎯 Market Found! ID: {new_id}")
                        return new_id
            return None
        except Exception as e:
            logger.error(f"Market Hunt Error: {e}")
            return None

    async def start_binance_feed(self):
        """Streams live BTC price"""
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

    async def check_arbitrage_loop(self):
        """Monitors for opportunities and triggers trades"""
        logger.info("🔍 Monitoring Spreads...")
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
                        logger.info(f"LIVE: BTC ${self.binance_price} | Poly Yes ${poly_ask}")

                    # --- TRIGGER LOGIC ---
                    # 1. Binance is moving UP (Momentum)
                    # 2. Poly "Yes" is still relatively cheap (< $0.92)
                    # 3. Not already in a trade
                    if is_pumping := (self.binance_price > self.last_binance_price):
                        if poly_ask < Decimal("0.92") and not self.is_trading:
                            await self.execute_trade(poly_ask)

            except Exception as e:
                if "404" in str(e):
                    self.active_id = None
                else:
                    logger.error(f"Orderbook Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_trade(self, price):
        """Places the order on Polymarket"""
        self.is_trading = True
        logger.info(f"🚀 SIGNAL DETECTED. Buying YES at ${price}")
        try:
            # Calculate shares to buy
            size = float(CONFIG["TRADE_AMOUNT_USDC"]) / float(price)
            
            order_args = OrderArgs(
                price=float(price),
                size=round(size, 1), # Poly usually wants 1 decimal for size
                side="BUY",
                token_id=self.active_id
            )
            
            resp = self.poly_client.create_order(order_args)
            logger.info(f"💰 TRADE PLACED: {resp}")
            
            # Cooldown: Wait for the next 15m window to avoid over-trading
            logger.info(f"💤 Entering cooldown for {CONFIG['COOLDOWN']}s...")
            await asyncio.sleep(CONFIG["COOLDOWN"]) 
        except Exception as e:
            logger.error(f"❌ TRADE FAILED: {e}")
        finally:
            self.is_trading = False

    async def run(self):
        logger.info("🚀 Bot Lifecycle Started - LIVE MODE")
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
