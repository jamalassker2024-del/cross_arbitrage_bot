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

# --- CONFIG ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,
    "MIN_SPREAD": Decimal("0.02"),
    "POLL_INTERVAL": 2,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LeanArb2026")

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.active_id = None
        self.is_trading = False
        # Initialize Polymarket Client immediately
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
            slug = f"bitcoin-price-above-below-15m-{ts}"
            url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
            resp = requests.get(url, timeout=10).json()
            if resp and len(resp) > 0:
                token_ids = json.loads(resp[0]['clobTokenIds'])
                return token_ids[0]
        except Exception as e:
            logger.error(f"Market Hunt Error: {e}")
        return None

    async def start_binance_feed(self):
        """Websocket feed with connection monitoring"""
        logger.info("📡 Connecting to Binance WebSocket...")
        try:
            # Added tld='us' just in case, but change to None if in Europe
            client = await AsyncClient.create(tld='us') 
            bm = BinanceSocketManager(client)
            ms = bm.symbol_ticker_socket('BTCUSDT')
            
            async with ms as tscm:
                logger.info("✅ Binance WebSocket Connected!")
                while True:
                    res = await tscm.recv()
                    if res:
                        self.binance_price = Decimal(res['c'])
        except Exception as e:
            logger.error(f"Binance Feed Critical Failure: {e}")
            await asyncio.sleep(5)

    async def check_arbitrage_loop(self):
        """Main execution logic"""
        logger.info("🔍 Starting Arbitrage Monitoring Loop...")
        while True:
            if not self.active_id:
                self.active_id = self.get_current_btc_token_id()
                if not self.active_id:
                    await asyncio.sleep(5)
                    continue

            if self.binance_price == 0:
                # Still waiting for first price from WebSocket
                await asyncio.sleep(1)
                continue

            try:
                book = self.poly_client.get_order_book(self.active_id)
                if book.asks:
                    poly_ask = Decimal(book.asks[0].price)
                    # Log status every few seconds
                    if int(time.time()) % 10 == 0:
                        logger.info(f"LIVE: BTC ${self.binance_price} | Poly ${poly_ask}")
            except Exception as e:
                logger.warning(f"Loop Warning: {e}")
                if "404" in str(e): self.active_id = None
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def run(self):
        logger.info("🚀 Bot Lifecycle Started")
        # Gather them but with specific names for better debugging
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop()
        )

if __name__ == "__main__":
    bot = ArbBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        pass
