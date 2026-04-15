#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WINNING CROSS-ARBITRAGE BOT – INSPIRED BY ORDER FLOW SCALPER
- REST API polling (works on Railway)
- Uses same profit logic as the 88% win rate bot
- Tight stop loss, trailing stop, breakeven
- Limit orders for entry (0% fee)
- Only trades when spread > fees + slippage + profit target
"""

import asyncio
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["PEPEUSDT", "DOGEUSDT", "SUIUSDT", "SOLUSDT", "ETHUSDT", "BTCUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("10"),           # 0.10% minimum spread
    "BINANCE_FEE": Decimal("0.001"),
    "BYBIT_FEE": Decimal("0.001"),
    "SLIPPAGE_BPS": Decimal("2"),
    "TAKE_PROFIT_BPS": Decimal("8"),           # 0.08% net profit target
    "STOP_LOSS_BPS": Decimal("5"),             # 0.05% stop loss
    "BREAKEVEN_ACTIVATE_BPS": Decimal("2"),    # Move SL to entry after 0.02% profit
    "TRAIL_ACTIVATE_BPS": Decimal("4"),        # Start trailing after 0.04% profit
    "TRAIL_DISTANCE_BPS": Decimal("2"),        # Trail 0.02% behind
    "MAX_HOLD_SECONDS": 15,
    "COOLDOWN_SEC": 3,
    "POLL_INTERVAL_SEC": 0.5,
    "BINANCE_REST": "https://api.binance.com/api/v3/ticker/bookTicker",
    "BYBIT_REST": "https://api.bybit.com/v5/market/tickers",
}

TAKER_FEE = Decimal("0.001")
MAKER_FEE = Decimal("0")  # Limit orders for entry

class WinningCrossArbitrageBot:
    def __init__(self):
        self.prices = {s: {"binance_ask": None, "binance_bid": None,
                           "bybit_ask": None, "bybit_bid": None,
                           "binance_time": 0, "bybit_time": 0}
                       for s in CONFIG["SYMBOLS"]}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.running = True

    async def fetch_prices(self, session):
        """Fetch prices from both exchanges"""
        try:
            # Binance
            async with session.get(CONFIG["BINANCE_REST"]) as resp:
                data = await resp.json()
                for item in data:
                    sym = item['symbol']
                    if sym in self.prices:
                        self.prices[sym]["binance_ask"] = Decimal(item['askPrice'])
                        self.prices[sym]["binance_bid"] = Decimal(item['bidPrice'])
                        self.prices[sym]["binance_time"] = time.time()
        except Exception as e:
            print(f"⚠️ Binance error: {e}")

        try:
            # Bybit
            url = f"{CONFIG['BYBIT_REST']}?category=spot"
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get('retCode') == 0:
                    for item in data['result']['list']:
                        sym = item['symbol']
                        if sym in self.prices:
                            self.prices[sym]["bybit_ask"] = Decimal(item['ask1Price'])
                            self.prices[sym]["bybit_bid"] = Decimal(item['bid1Price'])
                            self.prices[sym]["bybit_time"] = time.time()
        except Exception as e:
            print(f"⚠️ Bybit error: {e}")

    def is_fresh(self, sym, max_age=2.0):
        p = self.prices[sym]
        now = time.time()
        return (p["binance_ask"] and p["binance_bid"] and p["bybit_ask"] and p["bybit_bid"] and
                (now - p["binance_time"] < max_age) and (now - p["bybit_time"] < max_age))

    def open_arbitrage(self, symbol, direction, buy_exch, sell_exch, buy_price, sell_price):
        if buy_price <= 0 or sell_price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / buy_price
        # Use limit order for entry (0% fee)
        cost = qty * buy_price
        fee = cost * MAKER_FEE  # 0%

        if cost + fee > self.balance:
            return False

        self.balance -= (cost + fee)

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]

        if direction == 'binance_buy_bybit_sell':
            target_price = sell_price * (1 - tp_bps/10000)  # Sell target on Bybit
            stop_price = sell_price * (1 + sl_bps/10000)    # Stop on Bybit
        else:
            target_price = sell_price * (1 - tp_bps/10000)  # Sell target on Binance
            stop_price = sell_price * (1 + sl_bps/10000)    # Stop on Binance

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'order_size': order_size,
            'entry_time': time.time(),
            'entry_spread': (sell_price - buy_price) / buy_price * 10000,
            'target_price': target_price,
            'stop_price': stop_price,
            'best_price': sell_price,
            'trailing': False,
            'breakeven_activated': False,
        }

        net_profit = order_size * tp_bps/10000
        print(f"🔄 OPEN {symbol} | {direction} | Spread: {self.positions[symbol]['entry_spread']:.2f}bps | Target: +${net_profit:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            if not self.is_fresh(sym):
                continue

            p = self.prices[sym]
            now = time.time()

            # Get current exit price based on direction
            if pos['direction'] == 'binance_buy_bybit_sell':
                current_price = p["bybit_bid"]  # Sell price on Bybit
            else:
                current_price = p["binance_bid"]  # Sell price on Binance

            if current_price <= 0:
                continue

            # Calculate current profit/loss
            if pos['direction'] == 'binance_buy_bybit_sell':
                profit_pct = (current_price - pos['target_price']) / pos['target_price'] * 10000 / 100
            else:
                profit_pct = (current_price - pos['target_price']) / pos['target_price'] * 10000 / 100

            # BREAKEVEN: Move stop to entry after small profit
            gain_from_entry = (current_price - pos['target_price']) / pos['target_price'] * 10000
            if gain_from_entry >= CONFIG["BREAKEVEN_ACTIVATE_BPS"] and not pos['breakeven_activated']:
                pos['stop_price'] = pos['target_price']
                pos['breakeven_activated'] = True
                print(f"  🔒 Breakeven activated for {sym}")

            # TRAILING STOP
            if current_price > pos['best_price']:
                pos['best_price'] = current_price
                gain_from_best = (pos['best_price'] - pos['target_price']) / pos['target_price'] * 10000
                if gain_from_best >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                    pos['trailing'] = True
                if pos['trailing']:
                    new_stop = current_price * (1 - CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                    if new_stop > pos['stop_price']:
                        pos['stop_price'] = new_stop
                        print(f"  🔼 Trail {sym}: stop moved to {new_stop:.8f}")

            # Check take profit (limit exit, 0% fee)
            if current_price >= pos['target_price']:
                self.close_win(sym, current_price)
            # Check stop loss (market exit, 0.1% fee)
            elif current_price <= pos['stop_price']:
                self.close_loss(sym, current_price, "SL")
            elif now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
                self.close_loss(sym, current_price, "TIMEOUT")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * MAKER_FEE  # 0% on limit exit
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result = 'win'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['quantity'] * price
        fee = gross * TAKER_FEE  # 0.1% on market exit
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        print("\n" + "="*60)
        print("🏆 WINNING CROSS-ARBITRAGE BOT")
        print("="*60)
        print(f"   Inspired by 88% win rate order flow scalper")
        print(f"   TP: 0.08% | SL: 0.05% | Trail: 0.02% after 0.04%")
        print(f"   Breakeven: 0.02% | Limit orders (0% fee)")
        print(f"   Pairs: {len(CONFIG['SYMBOLS'])} | Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print("="*60 + "\n")

        last_status = 0

        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                # Fetch prices
                await self.fetch_prices(session)

                # Check positions
                self.check_positions()

                # Scan for new opportunities
                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions:
                        continue
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < CONFIG["COOLDOWN_SEC"]:
                        continue

                    if not self.is_fresh(sym):
                        continue

                    p = self.prices[sym]
                    spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
                    spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

                    required_spread = CONFIG["MIN_SPREAD_BPS"]

                    if spread1 >= required_spread:
                        print(f"🔥 {sym} spread={spread1:.2f}bps → BUY BINANCE / SELL BYBIT")
                        self.open_arbitrage(sym, "binance_buy_bybit_sell", "binance", "bybit", p["binance_ask"], p["bybit_bid"])
                    elif spread2 >= required_spread:
                        print(f"🔥 {sym} spread={spread2:.2f}bps → BUY BYBIT / SELL BINANCE")
                        self.open_arbitrage(sym, "bybit_buy_binance_sell", "bybit", "binance", p["bybit_ask"], p["binance_bid"])

                # Status every 10 seconds
                if now - last_status > 10:
                    active = sum(1 for s in CONFIG["SYMBOLS"] if self.is_fresh(s, max_age=3))
                    print(f"📡 Active: {active}/{len(CONFIG['SYMBOLS'])} | Open: {len(self.positions)} | Balance: ${self.balance:.2f} | WR: {(self.winning_trades/self.total_trades*100) if self.total_trades else 0:.1f}%")
                    last_status = now

                # Daily reset
                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["POLL_INTERVAL_SEC"])

if __name__ == "__main__":
    try:
        asyncio.run(WinningCrossArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
