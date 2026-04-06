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
    "MIN_SPREAD": Decimal("0.02"), # 2% spread target
    "POLL_INTERVAL": 2,            # Frequency of Poly checks
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("LeanArb2026")

class ArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
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
        """Fetches active market from Gamma API with User-Agent and slug verification"""
        try:
            ts = int(time.time() // 900) * 900
            
            # 2026 Standardized Slugs to try
            slug_options = [
                f"btc-updown-15m-{ts}", 
                f"bitcoin-price-above-below-15m-{ts}"
            ]
            
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            
            for slug in slug_options:
                url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
                logger.info(f"🔍 Hunting for: {slug}")
                
                resp = requests.get(url, headers=headers, timeout=10).json()
                
                if resp and len(resp) > 0:
                    raw_ids = resp[0].get('clobTokenIds')
                    if raw_ids:
                        token_ids = json.loads(raw_ids)
                        new_id = token_ids[0] # Index 0 is 'YES' (Above)
                        logger.info(f"🎯 Market Found! ID: {new_id}")
                        return new_id
            
            logger.warning("⚠️ No active 15m BTC markets found. Waiting for next interval...")
            return None
        except Exception as e:
            logger.error(f"Market Hunt Error: {e}")
        return None

    async def start_binance_feed(self):
        """Websocket feed with connection monitoring"""
        logger.info("📡 Connecting to Binance WebSocket...")
        try:
            # Using tld='us' to bypass common regional blocks
            client = await AsyncClient.create(tld='us') 
            bm = BinanceSocketManager(client)
            ms = bm.symbol_ticker_socket('BTCUSDT')
            
            async with ms as tscm:
                logger.info("✅ Binance WebSocket Connected!")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
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
                    await asyncio.sleep(10) # Wait 10s before retrying Gamma
                    continue

            if self.binance_price == 0:
                await asyncio.sleep(1)
                continue

            try:
                # Fixed method: get_order_book
                book = self.poly_client.get_order_book(self.active_id)
                if book and book.asks:
                    poly_ask = Decimal(book.asks[0].price)
                    
                    # Print status every 10s
                    if int(time.time()) % 10 == 0:
                        logger.info(f"LIVE: BTC ${self.binance_price} | Poly Yes ${poly_ask}")
                    
                    # SPREAD CALCULATION:
                    # If Binance price > Poly probability, Poly is "Cheap"
                    # Note: You need a baseline 'mid-price' for BTC to calculate true probability.
                    # For now, we are just logging the data.

            except Exception as e:
                if "404" in str(e):
                    logger.warning("Market expired. Resetting ID...")
                    self.active_id = None
                else:
                    logger.error(f"Orderbook Sync Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def run(self):
        logger.info("🚀 Bot Lifecycle Started")
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop()
        )

if __name__ == "__main__":
    bot = ArbBot()
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user.")
