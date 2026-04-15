#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MONEY PRINTING ARBITRAGE BOT – FULL STATS + AUTO TRADING
- Live price updates every 5 seconds
- Auto executes arbitrage when spread > fees
- Tracks P&L, win rate, and balance
- Prints money machine 🚀
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["btcusdt", "ethusdt", "solusdt", "pepeusdt", "dogeusdt"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "PRICE_JUMP_BPS": Decimal("2"),              # 0.02% jump threshold (more sensitive)
    "MIN_ARBITRAGE_BPS": Decimal("10"),          # 0.10% minimum spread (lower = more trades)
    "TAKE_PROFIT_BPS": Decimal("5"),             # 0.05% net profit target
    "STOP_LOSS_BPS": Decimal("6"),               # 0.06% stop loss
    "MAX_HOLD_SECONDS": 5,
    "COOLDOWN_SEC": 5,
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class MoneyPrintingBot:
    def __init__(self):
        self.binance_prices = {}
        self.binance_asks = {}
        self.binance_bids = {}
        self.bybit_prices = {}
        self.bybit_asks = {}
        self.bybit_bids = {}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.last_binance_time = {}
        self.last_bybit_time = {}
        self.running = True
        self.last_price_print = 0
        self.start_time = time.time()

    async def subscribe_binance_spot(self, symbol):
        """Binance Spot – bookTicker stream"""
        stream = f"{symbol}@bookTicker"
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        ask_price = Decimal(data['a'])
                        bid_price = Decimal(data['b'])
                        mid_price = (ask_price + bid_price) / 2
                        
                        old_price = self.binance_prices.get(symbol, mid_price)
                        self.binance_prices[symbol] = mid_price
                        self.binance_asks[symbol] = ask_price
                        self.binance_bids[symbol] = bid_price
                        self.last_binance_time[symbol] = time.time()
                        
                        # Detect price change
                        if old_price > 0 and mid_price != old_price:
                            change_bps = abs(mid_price - old_price) / old_price * 10000
                            if change_bps >= CONFIG["PRICE_JUMP_BPS"]:
                                self.check_arbitrage_opportunity(symbol, mid_price)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    async def subscribe_bybit_linear(self, symbol):
        """Bybit Linear – orderbook.50 stream"""
        while self.running:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    subscribe_msg = {"op": "subscribe", "args": [f"orderbook.50.{symbol.upper()}"]}
                    await ws.send(json.dumps(subscribe_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'topic' in data and 'data' in data:
                            book_data = data['data']
                            if 'b' in book_data and 'a' in book_data and book_data['b'] and book_data['a']:
                                best_bid = Decimal(book_data['b'][0][0])
                                best_ask = Decimal(book_data['a'][0][0])
                                mid_price = (best_bid + best_ask) / 2
                                self.bybit_prices[symbol] = mid_price
                                self.bybit_bids[symbol] = best_bid
                                self.bybit_asks[symbol] = best_ask
                                self.last_bybit_time[symbol] = time.time()
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}. Reconnecting...")
                await asyncio.sleep(5)

    def check_arbitrage_opportunity(self, symbol, binance_price):
        """Check if arbitrage opportunity exists and execute"""
        if symbol not in self.bybit_prices:
            return
        if symbol in self.positions:
            return
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return

        bybit_price = self.bybit_prices[symbol]
        if bybit_price <= 0:
            return

        # Calculate spread
        spread_bps = abs(binance_price - bybit_price) / min(binance_price, bybit_price) * 10000
        
        # Check if spread > minimum required
        if spread_bps < CONFIG["MIN_ARBITRAGE_BPS"]:
            return

        # Determine direction
        if binance_price > bybit_price:
            # Binance higher → Buy Bybit, Sell Binance
            print(f"\n💰💰💰 ARBITRAGE OPPORTUNITY on {symbol.upper()}! 💰💰💰")
            print(f"   📊 Spread: {spread_bps:.2f}bps > {CONFIG['MIN_ARBITRAGE_BPS']}bps")
            print(f"   🔵 BUY Bybit @ {bybit_price:.8f}")
            print(f"   🔴 SELL Binance @ {binance_price:.8f}")
            profit_estimate = CONFIG['ORDER_SIZE_USDT'] * (spread_bps - 10.5) / 10000
            print(f"   💵 Expected profit: +${profit_estimate:.5f}")
            self.execute_arbitrage(symbol, 'buy_bybit_sell_binance', bybit_price, binance_price, spread_bps)
        else:
            # Bybit higher → Buy Binance, Sell Bybit
            print(f"\n💰💰💰 ARBITRAGE OPPORTUNITY on {symbol.upper()}! 💰💰💰")
            print(f"   📊 Spread: {spread_bps:.2f}bps > {CONFIG['MIN_ARBITRAGE_BPS']}bps")
            print(f"   🔵 BUY Binance @ {binance_price:.8f}")
            print(f"   🔴 SELL Bybit @ {bybit_price:.8f}")
            profit_estimate = CONFIG['ORDER_SIZE_USDT'] * (spread_bps - 10.5) / 10000
            print(f"   💵 Expected profit: +${profit_estimate:.5f}")
            self.execute_arbitrage(symbol, 'buy_binance_sell_bybit', binance_price, bybit_price, spread_bps)

    def execute_arbitrage(self, symbol, direction, buy_price, sell_price, spread_bps):
        """Execute arbitrage trade"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                print(f"⚠️ Insufficient balance for {symbol}")
                return

        qty = order_size / buy_price
        entry_fee = order_size * Decimal("0.00055")
        total_cost = order_size + entry_fee

        if total_cost > self.balance:
            return

        self.balance -= total_cost

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]
        
        if direction == 'buy_bybit_sell_binance':
            target_price = sell_price * (1 - tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)
        else:
            target_price = sell_price * (1 - tp_bps/10000)
            stop_price = sell_price * (1 - sl_bps/10000)

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'buy_price': buy_price,
            'sell_price': sell_price,
            'target_price': target_price,
            'stop_price': stop_price,
        }

        expected_profit = order_size * (spread_bps - 10.5) / 10000
        print(f"✅✅✅ ARBITRAGE EXECUTED {symbol.upper()} ✅✅✅")
        print(f"   💰 Expected profit: +${expected_profit:.5f}")
        print(f"   💳 Balance after entry: ${self.balance:.2f}")
        self.last_trade_time[symbol] = time.time()

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if sym not in self.binance_prices:
                continue
            current_price = self.binance_prices[sym]
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
        fee = gross * Decimal("0.0005")
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.total_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"\n🎉🎉🎉 WIN {sym.upper()}! 🎉🎉🎉")
        print(f"   💵 Profit: +${profit:.5f}")
        print(f"   💳 New balance: ${self.balance:.2f}")
        print(f"   📊 Win rate: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * Decimal("0.0005")
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.total_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        print(f"\n❌ {reason} {sym.upper()} | Profit: ${profit:.5f} | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")

    async def run(self):
        # Start WebSocket connections
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.subscribe_binance_spot(sym))
            asyncio.create_task(self.subscribe_bybit_linear(sym))

        print("\n" + "="*70)
        print("💰💰💰 MONEY PRINTING ARBITRAGE BOT 💰💰💰")
        print("="*70)
        print(f"   📊 Min arbitrage spread: {CONFIG['MIN_ARBITRAGE_BPS']}bps")
        print(f"   💵 Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   🎯 Initial balance: ${self.balance}")
        print("="*70 + "\n")

        while self.running:
            now = time.time()
            
            # Print detailed live prices every 5 seconds
            if now - self.last_price_print > 5:
                runtime = (now - self.start_time) / 60  # minutes
                print("\n" + "="*70)
                print(f"📊 LIVE MARKET DATA - Runtime: {runtime:.1f} min")
                print("="*70)
                
                for sym in CONFIG["SYMBOLS"]:
                    binance = self.binance_prices.get(sym, 0)
                    bybit = self.bybit_prices.get(sym, 0)
                    binance_age = now - self.last_binance_time.get(sym, now) if sym in self.last_binance_time else 999
                    bybit_age = now - self.last_bybit_time.get(sym, now) if sym in self.last_bybit_time else 999
                    
                    if binance > 0 and bybit > 0:
                        spread = abs(binance - bybit) / min(binance, bybit) * 10000
                        spread_indicator = "🚀" if spread > 12 else "📊" if spread > 8 else "📉"
                        print(f"   {sym.upper():10} | Bin: {binance:.8f} | Byb: {bybit:.8f} | Spread: {spread:.2f}bps {spread_indicator}")
                    else:
                        print(f"   {sym.upper():10} | Bin: {binance:.8f} | Byb: {bybit:.8f} | ⏳ waiting for data")
                
                print("-"*70)
                print(f"💰 BALANCE: ${self.balance:.2f} | 💵 TOTAL PROFIT: +${self.total_profit:.5f}")
                print(f"📊 TRADES: {self.total_trades} | ✅ WINS: {self.winning_trades} | 📈 WIN RATE: {(self.winning_trades/self.total_trades*100) if self.total_trades else 0:.1f}%")
                print(f"🎯 OPEN POSITIONS: {len(self.positions)}")
                print("="*70 + "\n")
                self.last_price_print = now

            self.check_positions()
            await asyncio.sleep(0.5)

if __name__ == "__main__":
    try:
        asyncio.run(MoneyPrintingBot().run())
    except KeyboardInterrupt:
        print("\n🔴 Shutdown complete")
        print(f"💰 Final balance: ${self.balance:.2f}")
        print(f"💵 Total profit: +${self.total_profit:.5f}")
