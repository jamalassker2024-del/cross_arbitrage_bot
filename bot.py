import os
import asyncio
import json
import logging
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import OrderArgs

# --- 1. CONFIGURATION (Default Values for $200) ---
CONFIG = {
    "TRADE_AMOUNT_USDC": 15,        # $15 per trade
    "MIN_SPREAD": Decimal("0.018"), # 1.8% gap required
    "MARKET_ID": "0x...",           # The specific Polymarket Token ID
    "POLL_INTERVAL": 1,             # Seconds between Polymarket checks
}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LeanArb2026")

class ArbBot:
    def __init__(self):
        # Initialize Polymarket Client (Railway Env Vars)
        self.client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY"),
            chain_id=POLYGON,
            signature_type=1
        )
        self.binance_price = Decimal("0")
        self.is_trading = False

    async def get_binance_price(self):
        """Leader: Get real-time price from Binance WebSocket"""
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        ms = bm.symbol_ticker_socket('BTCUSDT')
        async with ms as tscm:
            while True:
                res = await tscm.recv()
                self.binance_price = Decimal(res['c'])

    async def check_arbitrage(self):
        """Lagger: Compare Binance to Polymarket Orderbook"""
        while True:
            if self.binance_price == 0:
                await asyncio.sleep(1)
                continue

            try:
                # Fetch Polymarket Orderbook
                book = self.client.get_order_book(CONFIG["MARKET_ID"])
                poly_ask = Decimal(book.asks[0].price) # Best 'Yes' price

                # Logic: If Binance goes UP, but Poly is still CHEAP
                spread = (self.binance_price - poly_ask) / poly_ask 

                if spread > CONFIG["MIN_SPREAD"] and not self.is_trading:
                    logger.info(f"🔥 OPPORTUNITY FOUND: {spread*100:.2f}%")
                    await self.execute_trade(poly_ask)

            except Exception as e:
                logger.error(f"Sync Error: {e}")
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_trade(self, price):
        """Execution: Place Limit Order to avoid high Taker Fees"""
        self.is_trading = True
        try:
            # 2026 Requirement: Must include feeRateBps in signature
            order_args = OrderArgs(
                price=float(price),
                size=CONFIG["TRADE_AMOUNT_USDC"] / float(price),
                side="BUY",
                token_id=CONFIG["MARKET_ID"]
            )
            resp = self.client.create_order(order_args)
            logger.info(f"✅ Trade Executed: {resp}")
        finally:
            self.is_trading = False

    async def run(self):
        await asyncio.gather(
            self.get_binance_price(),
            self.check_arbitrage()
        )

if __name__ == "__main__":
    bot = ArbBot()
    asyncio.run(bot.run())
