import os
import asyncio
import json
import logging
import requests
import time
import collections
from decimal import Decimal
from binance import AsyncClient, BinanceSocketManager
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OctoArb-Elite")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15.00,
    "POLL_INTERVAL": 1, 
    "COOLDOWN": 10,     
    "FEE_PERCENT": Decimal("0.001"),   
    "RSI_BUY_THRESHOLD": 50,    # Lowered from 52 to be more active
    "RSI_EXIT_THRESHOLD": 40,   # Emergency exit
    "MARKET_DURATION_SEC": 900, 
    "MAX_HISTORY": 50           
}

class EliteArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.price_history = collections.deque(maxlen=CONFIG["MAX_HISTORY"])
        self.active_id = None
        self.active_market_name = ""
        self.is_trading = False
        self.demo_balance = Decimal("200.00")
        self.wins = 0
        self.losses = 0
        
        # Initialize Client
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        try:
            # Enhanced query to find ANY active BTC market
            url = "https://gamma-api.polymarket.com/markets?active=true&closed=false&query=BTC&limit=10"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data:
                    # Look for markets that are open and have a valid Token ID
                    if m.get('clobTokenIds') and not m.get('closed'):
                        token_ids = json.loads(m['clobTokenIds'])
                        if token_ids:
                            logger.info(f"🎯 Target Acquired: {m.get('question')}")
                            return token_ids[0], m.get('question')
            return None, ""
        except Exception as e:
            logger.error(f"Error fetching token: {e}")
            return None, ""

    def calculate_rsi(self):
        if len(self.price_history) < 14: return 50.0
        prices = list(self.price_history)
        deltas = [float(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = abs(sum([d for d in deltas[-14:] if d < 0])) / 14
        if losses == 0: return 100.0
        rs = gains / losses
        return 100 - (100 / (1 + rs))

    async def start_binance_feed(self):
        client = await AsyncClient.create()
        bm = BinanceSocketManager(client)
        async with bm.symbol_ticker_socket('BTCUSDT') as tscm:
            while True:
                res = await tscm.recv()
                if res:
                    self.binance_price = Decimal(res['c'])
                    self.price_history.append(self.binance_price)

    async def trade_manager(self, entry_price, strike_at_trade, trade_id):
        start_t = time.time()
        logger.info(f"⏳ Trade #{trade_id} LIVE. Monitoring...")
        
        exit_reason = "EXPIRED"
        while (time.time() - start_t) < CONFIG["MARKET_DURATION_SEC"]:
            current_rsi = self.calculate_rsi()
            if current_rsi < CONFIG["RSI_EXIT_THRESHOLD"]:
                exit_reason = "STOP_LOSS_RSI"
                break
            await asyncio.sleep(5)

        # Settlement
        shares = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) / entry_price
        if exit_reason == "STOP_LOSS_RSI":
            payout = (Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * Decimal("0.3"))
            self.demo_balance += payout
            self.losses += 1
            logger.warning(f"🛑 EXIT: RSI Drop. Recovered: ${round(payout, 2)}")
        elif self.binance_price > strike_at_trade:
            payout = shares * Decimal("1.00")
            self.demo_balance += payout
            self.wins += 1
            logger.info(f"💰 WIN! Payout: ${round(payout, 2)}")
        else:
            self.losses += 1
            logger.info(f"📉 LOSS. BTC finished below entry.")
        
        self.is_trading = False

    async def check_arbitrage_loop(self):
        while True:
            if not self.active_id:
                self.active_id, self.active_market_name = self.fetch_active_token()
                await asyncio.sleep(2)
                continue

            try:
                # FIX: Use get_price('BUY') instead of get_order_book
                # On Polymarket, 'BUY' price is the price you pay to buy YES shares
                price_data = self.poly_client.get_price(self.active_id, side="BUY")
                poly_price = Decimal(str(price_data.get('price', '1.0')))
                rsi = self.calculate_rsi()

                # LOGGING TRIGGERS (Debugging)
                if poly_price > 0.90:
                    # If price is near 1.0, this market is already "decided", find a new one
                    self.active_id = None
                    continue

                if rsi > CONFIG["RSI_BUY_THRESHOLD"] and not self.is_trading:
                    trade_id = self.wins + self.losses + 1
                    cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"]))
                    self.demo_balance -= (cost + (cost * CONFIG["FEE_PERCENT"]))
                    
                    self.is_trading = True
                    logger.info(f"🚀 TRADE! RSI: {round(rsi,1)} | PolyPrice: {poly_price} | BTC: {self.binance_price}")
                    asyncio.create_task(self.trade_manager(poly_price, self.binance_price, trade_id))
                    await asyncio.sleep(CONFIG["COOLDOWN"])

            except Exception as e:
                logger.error(f"Loop Error: {e}")
                await asyncio.sleep(2)
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def heartbeat(self):
        while True:
            if self.binance_price > 0:
                total = self.wins + self.losses
                wr = (self.wins / total * 100) if total > 0 else 0
                logger.info(f"💓 BAL: ${round(self.demo_balance, 2)} | WR: {round(wr, 1)}% | RSI: {round(self.calculate_rsi(), 1)}")
            await asyncio.sleep(15)

    async def run(self):
        logger.info("🔥 Elite Bot Online. Triggering on live pricing...")
        await asyncio.gather(self.start_binance_feed(), self.check_arbitrage_loop(), self.heartbeat())

if __name__ == "__main__":
    asyncio.run(EliteArbBot().run())
