#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-EXCHANGE PRICE JUMP ARBITRAGE – BINANCE LEADS, BYBIT LAGS
- Detects when Binance price jumps relative to Bybit
- Executes immediately on Bybit BEFORE Bybit price updates
- Captures the latency arbitrage window
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "OFI_THRESHOLD": Decimal("0.65"),           # Binance OFI threshold
    "PRICE_JUMP_BPS": Decimal("5"),             # 0.05% price jump threshold
    "BINANCE_FEE": Decimal("0.0005"),           # 0.05% taker
    "BYBIT_FEE": Decimal("0.00055"),            # 0.055% taker
    "TOTAL_FEES_BPS": Decimal("10.5"),          # 0.105% combined
    "TAKE_PROFIT_BPS": Decimal("8"),            # 0.08% net profit
    "STOP_LOSS_BPS": Decimal("10"),             # 0.10% stop loss
    "MAX_HOLD_SECONDS": 5,
    "COOLDOWN_SEC": 3,
    "SCAN_INTERVAL_MS": 10,                    # 10ms scan for fast detection
    "BINANCE_FUTURES_WS": "wss://fstream.binance.com/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class PriceJumpArbitrageBot:
    def __init__(self):
        self.binance_books = {}
        self.bybit_books = {}
        self.binance_prices = {}
        self.bybit_prices = {}
        self.binance_price_history = {}  # Track price changes
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}
            self.last_price = Decimal('0')
            self.last_update = 0.0

        def apply_snapshot(self, bids, asks):
            self.bids = {Decimal(p): Decimal(q) for p, q in bids[:20]}
            self.asks = {Decimal(p): Decimal(q) for p, q in asks[:20]}
            self.last_update = time.time()

        def apply_delta(self, bids, asks):
            for price, qty in bids:
                p, q = Decimal(price), Decimal(qty)
                if q == 0:
                    self.bids.pop(p, None)
                else:
                    self.bids[p] = q
            for price, qty in asks:
                p, q = Decimal(price), Decimal(qty)
                if q == 0:
                    self.asks.pop(p, None)
                else:
                    self.asks[p] = q
            self.last_update = time.time()

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def mid_price(self):
            bb, ba = self.best_bid(), self.best_ask()
            return (bb + ba) / 2 if bb and ba else Decimal('0')

        def get_ofi(self, depth=10):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_bids))
            ask_vol = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_asks))
            total = bid_vol + ask_vol
            if total == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / total

    async def subscribe_binance_futures(self, symbol):
        """Binance Futures WebSocket – detect price jumps"""
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_FUTURES_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance Futures {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if symbol not in self.binance_books:
                            self.binance_books[symbol] = self.OrderBook(symbol)
                        
                        old_price = self.binance_prices.get(symbol, Decimal('0'))
                        
                        if 'bids' in data and 'asks' in data:
                            self.binance_books[symbol].apply_snapshot(data['bids'], data['asks'])
                        else:
                            self.binance_books[symbol].apply_delta(data.get('b', []), data.get('a', []))
                        
                        new_price = self.binance_books[symbol].mid_price()
                        self.binance_prices[symbol] = new_price
                        self.last_binance_time[symbol] = time.time()
                        
                        # DETECT PRICE JUMP ON BINANCE
                        if old_price > 0 and new_price > 0:
                            price_change_bps = abs(new_price - old_price) / old_price * 10000
                            direction = new_price > old_price
                            
                            # If price jumped UP on Binance
                            if price_change_bps >= CONFIG["PRICE_JUMP_BPS"] and direction:
                                print(f"🚨 PRICE JUMP DETECTED on {symbol}: {old_price:.8f} → {new_price:.8f} (+{price_change_bps:.2f}bps)")
                                # Check if Bybit price hasn't caught up yet
                                if symbol in self.bybit_prices:
                                    bybit_price = self.bybit_prices[symbol]
                                    spread_bps = (new_price - bybit_price) / bybit_price * 10000
                                    if spread_bps > CONFIG["TOTAL_FEES_BPS"]:
                                        print(f"🎯 ARBITRAGE OPPORTUNITY! Binance jumped, Bybit lags by {spread_bps:.2f}bps")
                                        self.execute_arbitrage(symbol, 'buy_bybit_sell_binance', bybit_price, new_price)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def subscribe_bybit_linear(self, symbol):
        """Bybit Linear WebSocket"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    print(f"✅ Bybit Linear {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'topic' in data and 'data' in data:
                            if symbol not in self.bybit_books:
                                self.bybit_books[symbol] = self.OrderBook(symbol)
                            book_data = data['data']
                            if 'b' in book_data and 'a' in book_data:
                                self.bybit_books[symbol].apply_snapshot(book_data['b'], book_data['a'])
                                self.bybit_prices[symbol] = self.bybit_books[symbol].mid_price()
                                self.last_bybit_time[symbol] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    def execute_arbitrage(self, symbol, direction, buy_price, sell_price):
        """Execute arbitrage: buy on lagging exchange, sell on leading exchange"""
        if symbol in self.positions:
            return False
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / buy_price
        entry_fee = order_size * CONFIG["BYBIT_FEE"]

        if order_size + entry_fee > self.balance:
            return False

        self.balance -= (order_size + entry_fee)

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]

        # Target price is where we sell (Binance price after jump)
        target_price = sell_price * (1 - tp_bps/10000)  # Small profit target
        stop_price = sell_price * (1 - sl_bps/10000)

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'target_price': target_price,
            'stop_price': stop_price,
            'entry_price': buy_price,
            'sell_price': sell_price,
        }

        net_profit = order_size * tp_bps/10000
        print(f"⚡ ARBITRAGE EXECUTED {symbol} | Buy Bybit @ {buy_price:.8f} | Sell Binance @ {sell_price:.8f} | Target net: +${net_profit:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if sym not in self.binance_prices:
                continue
            current_price = self.binance_prices[sym]
            if current_price <= 0:
                continue

            now = time.time()

            if current_price >= pos['target_price']:
                self.close_win(sym, current_price)
            elif current_price <= pos['stop_price']:
                self.close_loss(sym, current_price, "SL")
            elif now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
                self.close_loss(sym, current_price, "TIMEOUT")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * CONFIG["BINANCE_FEE"]
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * CONFIG["BINANCE_FEE"]
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | ${profit:.5f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_futures(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*60)
        print("🚀 CROSS-EXCHANGE PRICE JUMP ARBITRAGE")
        print("="*60)
        print(f"   Strategy: Binance leads, Bybit lags")
        print(f"   Price jump threshold: {CONFIG['PRICE_JUMP_BPS']}bps")
        print(f"   Min profit required: {CONFIG['TOTAL_FEES_BPS']}bps (fees)")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("="*60 + "\n")

        last_status = 0

        while self.running:
            now = time.time()

            # Status every 10 seconds
            if now - last_status > 10:
                print(f"\n📡 STATUS | Balance: ${self.balance:.2f} | Open positions: {len(self.positions)} | Trades: {self.total_trades} | WR: {(self.winning_trades/self.total_trades*100) if self.total_trades else 0:.1f}%")
                last_status = now

            self.check_positions()

            # Daily reset
            if now - self.daily_start >= 86400:
                print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                self.daily_profit = Decimal('0')
                self.daily_start = now

            await asyncio.sleep(0.05)  # 50ms loop

if __name__ == "__main__":
    try:
        asyncio.run(PriceJumpArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
