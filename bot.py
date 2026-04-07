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
logger = logging.getLogger("OctoArb-Unified")

CONFIG = {
    "TRADE_AMOUNT_USDC": 15.00,
    "POLL_INTERVAL": 1, 
    "COOLDOWN": 10,     
    "FEE_PERCENT": Decimal("0.001"),   
    "RSI_THRESHOLD": 48, 
    "MARKET_DURATION_SEC": 900, # 15-minute settlement windows
    "MAX_HISTORY": 30
}

class ArbBot:
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
        
        # Client Setup
        self.poly_client = ClobClient(
            host="https://clob.polymarket.com",
            key=os.getenv("PRIVATE_KEY") or "0x" + "1"*64, 
            chain_id=POLYGON,
            signature_type=1
        )

    def fetch_active_token(self):
        """Finds the newest BTC market on Polymarket without external dependencies"""
        try:
            url = "https://gamma-api.polymarket.com/markets?active=true&limit=5&query=BTC&order=createdAt&ascending=false"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for m in data:
                    if not m.get('closed') and m.get('clobTokenIds'):
                        token_id = json.loads(m['clobTokenIds'])[0]
                        question = m.get('question', 'Unknown Market')
                        logger.info(f"🎯 New Market Target: {question}")
                        return token_id, question
            return None, ""
        except Exception as e:
            logger.error(f"Market Discovery Error: {e}")
            return None, ""

    def calculate_indicators(self):
        """Native Python RSI & RDI (No Numpy required)"""
        if len(self.price_history) < 14:
            return 50.0, 0.0
        
        prices = list(self.price_history)
        deltas = [float(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        
        # Simple RSI Math
        gains = sum([d for d in deltas[-14:] if d > 0]) / 14
        losses = abs(sum([d for d in deltas[-14:] if d < 0])) / 14
        
        rs = gains / (losses if losses > 0 else 0.00001)
        rsi = 100 - (100 / (1 + rs))
        
        # RDI (Directional Index): Difference between price and 5-period average
        avg_5 = sum(prices[-5:]) / 5
        rdi = float(prices[-1]) - float(avg_5)
        
        return rsi, rdi

    async def start_binance_feed(self):
        """Streams real-time BTC prices from Binance"""
        try:
            client = await AsyncClient.create()
            bm = BinanceSocketManager(client)
            async with bm.symbol_ticker_socket('BTCUSDT') as tscm:
                logger.info("✅ Binance WebSocket Connected")
                while True:
                    res = await tscm.recv()
                    if res and 'c' in res:
                        self.binance_price = Decimal(res['c'])
                        self.price_history.append(self.binance_price)
        except Exception as e:
            logger.error(f"Binance Connection Lost: {e}")
            await asyncio.sleep(5)

    async def settle_trade(self, entry_price, strike_at_trade, trade_id):
        """Simulates 15-minute settlement and tracks PnL"""
        logger.info(f"⏳ Trade #{trade_id} locked. Settling in {CONFIG['MARKET_DURATION_SEC']/60}m...")
        await asyncio.sleep(CONFIG["MARKET_DURATION_SEC"])
        
        current_btc = self.binance_price
        shares_bought = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) / entry_price
        
        if current_btc > strike_at_trade:
            # Winning shares pay out $1.00 each
            payout = shares_bought * Decimal("1.00")
            self.demo_balance += payout
            self.wins += 1
            logger.info(f"💰 WIN! BTC ${current_btc} > Strike ${strike_at_trade}. Payout: ${round(payout, 2)}")
        else:
            self.losses += 1
            logger.info(f"📉 LOSS. BTC ${current_btc} <= Strike ${strike_at_trade}. Position expired.")

    async def check_arbitrage_loop(self):
        """Main strategy loop: Checks RSI momentum vs Polymarket price lag"""
        while True:
            if not self.active_id:
                self.active_id, self.active_market_name = self.fetch_active_token()
                if not self.active_id:
                    await asyncio.sleep(5)
                    continue

            try:
                book = self.poly_client.get_order_book(self.active_id)
                if hasattr(book, 'asks') and book.asks:
                    poly_price = Decimal(str(book.asks[0].price))
                    rsi, rdi = self.calculate_indicators()

                    # STRATEGY: Bullish RSI + Positive Momentum + Lagging Poly Price
                    if rsi > CONFIG["RSI_THRESHOLD"] and rdi > 0 and poly_price < 0.85:
                        if not self.is_trading and self.demo_balance > CONFIG["TRADE_AMOUNT_USDC"]:
                            await self.execute_trade_flow(poly_price, rsi)

            except Exception as e:
                if "404" in str(e) or "No orderbook" in str(e):
                    self.active_id = None
                await asyncio.sleep(2)
            
            await asyncio.sleep(CONFIG["POLL_INTERVAL"])

    async def execute_trade_flow(self, poly_price, rsi):
        """Handles the 'Buy' and kicks off the background settlement task"""
        self.is_trading = True
        trade_id = self.wins + self.losses + 1
        
        fee = Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) * CONFIG["FEE_PERCENT"]
        self.demo_balance -= (Decimal(str(CONFIG["TRADE_AMOUNT_USDC"])) + fee)
        
        strike = self.binance_price
        logger.info(f"🚀 TRADE #{trade_id} EXECUTED")
        logger.info(f"Context: RSI {round(rsi, 1)} | Price: {poly_price} | BTC Strike: ${strike}")
        
        # Run settlement in background so bot can keep monitoring
        asyncio.create_task(self.settle_trade(poly_price, strike, trade_id))
        
        await asyncio.sleep(CONFIG["COOLDOWN"])
        self.is_trading = False

    async def heartbeat(self):
        """Displays status updates every 20 seconds"""
        while True:
            if self.binance_price > 0:
                uptime = round((time.time() - self.start_time) / 60, 1)
                total_trades = self.wins + self.losses
                win_rate = (self.wins / total_trades * 100) if total_trades > 0 else 0
                
                logger.info("--- 💓 HEARTBEAT ---")
                logger.info(f"UP: {uptime}m | BTC: ${self.binance_price} | Bal: ${round(self.demo_balance, 2)}")
                logger.info(f"Stats: {self.wins}W - {self.losses}L ({round(win_rate, 1)}%)")
                logger.info("--------------------")
            await asyncio.sleep(20)

    async def run(self):
        logger.info("🤖 Bot starting up...")
        await asyncio.gather(
            self.start_binance_feed(),
            self.check_arbitrage_loop(),
            self.heartbeat()
        )

if __name__ == "__main__":
    try:
        asyncio.run(ArbBot().run())
    except KeyboardInterrupt:
        logger.info("🛑 Bot stopped by user.")
