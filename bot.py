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

# --- 1. CONFIGURATION ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,        # $15 per trade
    "MIN_SPREAD": Decimal("0.02"),  # 2.0% gap required (better for $200 bankroll)
    "POLL_INTERVAL": 2,             # Slightly slower to avoid 429 Rate Limits
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LeanArb2026")

class ArbBot:
    def __init__(self):
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY"),
            chain_id=POLYGON,
            signature_type=1
        )
        self.binance_price = Decimal("0")
        self.active_id = None
        self.is_trading = False

    def get_current_btc_token_id(self):
        """Finds the current active 15m BTC 'Above' market ID"""
        try:
            # Round down to the nearest 15 mins (900 seconds)
            ts = int(time.time() // 900) * 900
            slug = f"bitcoin-price-above-below-15m-{ts}" # 2026 Slug Format
            
            url = f"https://gamma-api.polymarket.com/markets?slug={slug}"
            resp = requests.get(url).json()
            
            if resp and len(resp) > 0:
                # Index 0 is typically the 'YES' (Above) token
                token_ids = json.loads(resp[0]['clobTokenIds'])
                token_id = token_ids[0]
                logger.info(f"🎯 Linked to Market: {slug} | ID: {token_id}")
                return token_id
        except Exception as e:
            logger.error(f"Market Hunt Failed: {e}")
        return None

    async def get_binance_price(self):
        """Leader: Fast Price Feed"""
        # Note: If still in US region, change to tld='us'
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        ms = bm.symbol_ticker_socket('BTCUSDT')
        async with ms as tscm:
            while True:
                res = await tscm.recv()
                self.binance_price = Decimal(res['c'])

    async def check_arbitrage(self):
        """Lagger: Execution Logic"""
        while True:
            # Refresh Market ID every loop if missing
            if not self.active_id:
                self.active_id = self.get_current_btc_token_id()
                if not self.active_id:
                    await asyncio.sleep(10)
                    continue

            if self.binance_price == 0:
                await asyncio.sleep(1)
                continue

            try:
                # 1. Fetch Order Book (Fixed method name)
                book = self.client.get_order_book(self.active_id)
                
                if not book.asks:
                    continue
                    
                poly_ask = Decimal(book.asks[0].price) # Price of 'YES' (e.g. 0.50)

                # 2. Arbitrage Logic for Prediction Markets
                # Note: In prediction markets, we compare the 'Probability' 
                # against the Binance trend, not just raw price.
                # Here we log the spread for your $2/day goal:
                
                logger.info(f"Binance: {self.binance_price} | Poly Yes: {poly_ask}")

                # Simple example: if Binance is pumping but 'YES' is still 0.45 (45%)
                # you would place a trade here.
                
                # if spread > CONFIG["MIN_SPREAD"] and not self.is_trading:
                #     await self.execute_trade(poly_ask)

            except Exception as e:
                if "404" in str(e):
                    logger.warning("Market expired. Hunting new ID...")
                    self.active_id = None # Trigger re-hunt
                else:
                    logger.error(f"Sync Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_trade(self, price):
        self.is_trading = True
        try:
            order_args = OrderArgs(
                price=float(price),
                size=CONFIG["TRADE_AMOUNT_USDC"] / float(price),
                side="BUY",
                token_id=self.active_id
            )
            resp = self.client.create_order(order_args)
            logger.info(f"✅ Trade Executed: {resp}")
        except Exception as e:
            logger.error(f"Trade Failed: {e}")
        finally:
            self.is_trading = False

    async def run(self):
        logger.info("Bot initializing...")
        await asyncio.gather(
            self.get_binance_price(),
            self.check_arbitrage()
        )

if __name__ == "__main__":
    bot = ArbBot()
    asyncio.run(bot.run())
