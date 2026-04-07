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
    "COOLDOWN": 20,     
    "FEE_PERCENT": Decimal("0.001"),   
    "RSI_BUY_THRESHOLD": 52,    # Enter when momentum is strong
    "RSI_EXIT_THRESHOLD": 42,   # Emergency exit if momentum dies
    "MARKET_DURATION_SEC": 900, # 15-minute max hold
    "MAX_HISTORY": 50           # Larger buffer for smoother RSI
}

class EliteArbBot:
    def __init__(self):
        self.binance_price = Decimal("0")
        self.price_history = collections.deque(maxlen=CONFIG["MAX_HISTORY"])
        self.active_id = None
        self.active_market_name = ""
        self.is_trading = False
        
        # Performance Tracking
        self.demo_balance = Decimal("200.00")
        self.wins = 0
        self.losses = 0
        self.start_time = time.time()
        
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        try:
            url = "https://gamma-api.polymarket.com/markets?active=true&limit=5&query=BTC&order=createdAt&ascending=false"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data:
                    if not m.get('closed') and m.get('clobTokenIds'):
                        token_id = json.loads(m['clobTokenIds'])[0]
                        return token_id, m.get('question', 'BTC Market')
            return None, ""
        except: return None, ""

    def calculate_rsi(self):
        if len(self.price_history) < 14: return 50.0
        prices = list(self.price_history)
        deltas = [float(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = abs(sum([d for d in deltas[-14:] if d < 0])) / 14
        rs = gains / (losses if losses > 0 else 0.00001)
        return 100 - (100 / (1 + rs))

    async def start_binance_feed(self):
        try:
            client = await AsyncClient.create()
            bm = BinanceSocketManager(client)
            async with bm.symbol_ticker_socket('BTCUSDT') as tscm:
                while True:
                    res = await tscm.recv()
                    if res:
                        self.binance_price = Decimal(res['c'])
                        self.price_history.append(self.binance_price)
        except: await asyncio.sleep(5)

    async def trade_manager(self, entry_price, strike_at_trade, trade_id):
        """
        Manages the life of a trade. 
        It will close if:
        1. Time expires (15 mins)
        2. RSI drops below EXIT_THRESHOLD (Stop Loss)
        """
        start_t = time.time()
        logger.info(f"⏳ Trade #{trade_id} started. Monitoring for Exit signals...")
        
        exit_reason = "EXPIRED"
        while (time.time() - start_t) < CONFIG["MARKET_DURATION_SEC"]:
            current_rsi = self.calculate_rsi()
            
            # STOP LOSS: If RSI collapses, we 'sell' early to save 50% of the bet
            if current_rsi < CONFIG["RSI_EXIT_THRESHOLD"]:
                exit_reason = "STOP_LOSS_RSI"
                break
            await asyncio.sleep(2)

        # SETTLEMENT LOGIC
        current_btc = self.binance_price
        shares = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) / entry_price
        
        if exit_reason == "STOP_LOSS_RSI":
            # Simulate selling back at a loss (0.4x entry) instead of 0
            payout = (Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * Decimal("0.4"))
            self.demo_balance += payout
            self.losses += 1
            logger.warning(f"🛑 STOP LOSS TRIGGERED #{trade_id}. Recovered: ${round(payout, 2)}")
        elif current_btc > strike_at_trade:
            payout = shares * Decimal("1.00")
            self.demo_balance += payout
            self.wins += 1
            logger.info(f"💰 WIN! Trade #{trade_id} Payout: ${round(payout, 2)}")
        else:
            self.losses += 1
            logger.info(f"📉 LOSS. Trade #{trade_id} expired worthless.")

    async def check_arbitrage_loop(self):
        while True:
            if not self.active_id:
                self.active_id, self.active_market_name = self.fetch_active_token()
                if not self.active_id:
                    await asyncio.sleep(5); continue

            try:
                book = self.poly_client.get_order_book(self.active_id)
                if hasattr(book, 'asks') and book.asks:
                    poly_price = Decimal(str(book.asks[0].price))
                    rsi = self.calculate_rsi()

                    # Entry: High RSI + Low Poly Price
                    if rsi > CONFIG["RSI_BUY_THRESHOLD"] and poly_price < 0.82:
                        if not self.is_trading and self.demo_balance > 20:
                            self.is_trading = True
                            trade_id = self.wins + self.losses + 1
                            
                            # Deduct Cost + Fee
                            cost = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"]))
                            self.demo_balance -= (cost + (cost * CONFIG["FEE_PERCENT"]))
                            
                            logger.info(f"🚀 ENTERING TRADE #{trade_id} | RSI: {round(rsi,1)} | Price: {poly_price}")
                            
                            # Manage this specific trade in the background
                            asyncio.create_task(self.trade_manager(poly_price, self.binance_price, trade_id))
                            
                            await asyncio.sleep(CONFIG["COOLDOWN"])
                            self.is_trading = False

            except Exception as e:
                if "404" in str(e): self.active_id = None
                await asyncio.sleep(2)
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def heartbeat(self):
        while True:
            if self.binance_price > 0:
                total = self.wins + self.losses
                wr = (self.wins / total * 100) if total > 0 else 0
                logger.info(f"💓 BAL: ${round(self.demo_balance, 2)} | WINRATE: {round(wr, 1)}% | BTC: ${self.binance_price}")
            await asyncio.sleep(30)

    async def run(self):
        logger.info("🔥 Elite Bot Online. Target: $1/Day Consistency.")
        await asyncio.gather(self.start_binance_feed(), self.check_arbitrage_loop(), self.heartbeat())

if __name__ == "__main__":
    asyncio.run(EliteArbBot().run())
