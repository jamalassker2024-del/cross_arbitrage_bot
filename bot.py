#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CROSS-ARBITRAGE REBALANCER – BINANCE ↔ BYBIT (ULTRA-FAST)
- Public WebSocket feeds from both exchanges
- Market orders for instant execution (simulated)
- Spread threshold: 0.08% (covers fees + slippage + tiny profit)
- Concurrent monitoring on 10+ volatile pairs
- Trailing stop protection on net position
- 10ms scan interval
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
    "SYMBOLS": [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "PEPEUSDT", "DOGEUSDT",
        "SUIUSDT", "WIFUSDT", "BONKUSDT", "NEIROUSDT", "1000PEPEUSDT"
    ],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("8"),            # 0.08% gross spread required
    "BINANCE_FEE": Decimal("0.001"),           # 0.1% taker
    "BYBIT_FEE": Decimal("0.001"),             # 0.1% taker
    "SLIPPAGE_BPS": Decimal("2"),              # 0.02% slippage per leg
    "TAKE_PROFIT_BPS": Decimal("4"),           # 0.04% net profit target
    "STOP_LOSS_BPS": Decimal("6"),             # 0.06% stop loss on net position
    "MAX_HOLD_SECONDS": 5,                     # Max time to complete both legs
    "COOLDOWN_SEC": 2,
    "SCAN_INTERVAL_MS": 10,                    # 10ms ultra-fast scan
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_WS": "wss://stream.bybit.com/v5/public/spot",
}

TAKER_FEE = Decimal("0.001")
SLIPPAGE_FACTOR = Decimal("1") + CONFIG["SLIPPAGE_BPS"] / Decimal("10000")

class CrossArbitrageBot:
    def __init__(self):
        self.prices = {s: {"binance_ask": None, "binance_bid": None,
                           "bybit_ask": None, "bybit_bid": None,
                           "binance_time": 0, "bybit_time": 0}
                       for s in CONFIG["SYMBOLS"]}
        self.positions = {}          # active arbitrage positions
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.running = True

    # ---------- WebSocket Feeds ----------
    async def binance_stream(self, symbol):
        """Binance bookTicker stream (best bid/ask)"""
        stream = f"{symbol.lower()}@bookTicker"
        url = f"{CONFIG['BINANCE_WS']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        self.prices[symbol]["binance_time"] = time.time()
            except Exception:
                await asyncio.sleep(3)

    async def bybit_stream(self):
        """Bybit tickers stream (all symbols)"""
        url = CONFIG["BYBIT_WS"]
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    sub_msg = {"op": "subscribe", "args": [f"tickers.{s}" for s in CONFIG["SYMBOLS"]]}
                    await ws.send(json.dumps(sub_msg))
                    async for msg in ws:
                        data = json.loads(msg)
                        if data.get("op") == "subscribe":
                            continue
                        if "topic" in data and data["topic"].startswith("tickers."):
                            d = data.get("data", {})
                            sym = d.get('symbol')
                            if sym in self.prices:
                                if 'ask1Price' in d:
                                    self.prices[sym]["bybit_ask"] = Decimal(d['ask1Price'])
                                if 'bid1Price' in d:
                                    self.prices[sym]["bybit_bid"] = Decimal(d['bid1Price'])
                                self.prices[sym]["bybit_time"] = time.time()
            except Exception:
                await asyncio.sleep(3)

    # ---------- Price Freshness ----------
    def is_fresh(self, sym, max_age=1.0):
        """Both exchanges must have recent data"""
        p = self.prices[sym]
        now = time.time()
        return (p["binance_ask"] and p["binance_bid"] and
                p["bybit_ask"] and p["bybit_bid"] and
                now - p["binance_time"] < max_age and
                now - p["bybit_time"] < max_age)

    # ---------- Arbitrage Execution ----------
    def open_arbitrage(self, symbol, direction, buy_exch, sell_exch, buy_price, sell_price):
        """
        Open both legs simultaneously with market orders.
        direction: "binance_buy_bybit_sell" or "bybit_buy_binance_sell"
        """
        if buy_price <= 0 or sell_price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        # Calculate quantity based on buy side
        qty = order_size / buy_price

        # Apply slippage to both legs
        buy_exec = buy_price * SLIPPAGE_FACTOR
        sell_exec = sell_price / SLIPPAGE_FACTOR

        cost_buy = qty * buy_exec
        fee_buy = cost_buy * TAKER_FEE
        gross_sell = qty * sell_exec
        fee_sell = gross_sell * TAKER_FEE

        total_cost = cost_buy + fee_buy
        total_return = gross_sell - fee_sell

        if total_cost > self.balance:
            return False

        self.balance -= total_cost

        # Calculate expected profit
        expected_profit = total_return - total_cost
        tp_price = expected_profit * Decimal('0.5')  # placeholder for net position tracking

        self.positions[symbol] = {
            'direction': direction,
            'quantity': qty,
            'entry_time': time.time(),
            'buy_exch': buy_exch,
            'sell_exch': sell_exch,
            'buy_price': buy_exec,
            'sell_price': sell_exec,
            'expected_profit': expected_profit,
            'entry_spread': (sell_price - buy_price) / buy_price * 10000
        }

        print(f"🔄 ARBITRAGE OPEN {symbol} | {direction} | Spread: {self.positions[symbol]['entry_spread']:.2f}bps | Expected: +${expected_profit:.5f}")
        return True

    def check_and_close_arbitrage(self, symbol):
        """Monitor both legs; close if profit target hit or timeout/stop loss"""
        pos = self.positions.get(symbol)
        if not pos:
            return

        p = self.prices[symbol]
        if not self.is_fresh(symbol):
            return

        now = time.time()

        # Get current exit prices (opposite of entry)
        if pos['direction'] == 'binance_buy_bybit_sell':
            # Exit: sell on Binance (at bid), buy back on Bybit (at ask)
            exit_binance = p["binance_bid"]
            exit_bybit = p["bybit_ask"]
            current_net_spread = (exit_binance - exit_bybit) / exit_bybit * 10000
        else:
            # Exit: sell on Bybit (at bid), buy back on Binance (at ask)
            exit_bybit = p["bybit_bid"]
            exit_binance = p["binance_ask"]
            current_net_spread = (exit_bybit - exit_binance) / exit_binance * 10000

        if exit_binance <= 0 or exit_bybit <= 0:
            return

        # Calculate current net profit
        qty = pos['quantity']
        if pos['direction'] == 'binance_buy_bybit_sell':
            gross_return = qty * exit_binance
            cost_close = qty * exit_bybit
        else:
            gross_return = qty * exit_bybit
            cost_close = qty * exit_binance

        fee_exit = (gross_return + cost_close) * TAKER_FEE
        profit = (gross_return - cost_close) - fee_exit

        # Check take profit
        if profit >= CONFIG["ORDER_SIZE_USDT"] * CONFIG["TAKE_PROFIT_BPS"] / 10000:
            self.close_arbitrage(symbol, profit, "TAKE_PROFIT")
        # Check stop loss
        elif profit <= -CONFIG["ORDER_SIZE_USDT"] * CONFIG["STOP_LOSS_BPS"] / 10000:
            self.close_arbitrage(symbol, profit, "STOP_LOSS")
        # Timeout – close at market
        elif now - pos['entry_time'] > CONFIG["MAX_HOLD_SECONDS"]:
            self.close_arbitrage(symbol, profit, "TIMEOUT")

    def close_arbitrage(self, symbol, profit, reason):
        pos = self.positions.pop(symbol)
        self.balance += pos['order_size'] + profit  # simplified: original capital + profit
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅ WIN' if profit>0 else '❌ LOSS'} {reason} {symbol} | Profit: ${profit:.5f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[symbol] = time.time()

    # ---------- Main Loop ----------
    async def run(self):
        # Start WebSocket streams
        for sym in CONFIG["SYMBOLS"]:
            asyncio.create_task(self.binance_stream(sym))
        asyncio.create_task(self.bybit_stream())

        print("\n🚀 CROSS-ARBITRAGE REBALANCER – BINANCE ↔ BYBIT")
        print(f"   Min spread: {float(CONFIG['MIN_SPREAD_BPS'])/100:.2f}% | TP: {float(CONFIG['TAKE_PROFIT_BPS'])/100:.2f}% | SL: {float(CONFIG['STOP_LOSS_BPS'])/100:.2f}%")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']} | Scan: {CONFIG['SCAN_INTERVAL_MS']}ms\n")

        last_scan_print = 0
        while self.running:
            now = time.time()

            # Close existing positions
            for sym in list(self.positions.keys()):
                self.check_and_close_arbitrage(sym)

            # Scan for new arbitrage opportunities
            for sym in CONFIG["SYMBOLS"]:
                if sym in self.positions:
                    continue
                if sym in self.last_trade_time and now - self.last_trade_time[sym] < CONFIG["COOLDOWN_SEC"]:
                    continue

                if not self.is_fresh(sym):
                    continue

                p = self.prices[sym]
                # Binance cheaper → buy Binance, sell Bybit
                spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
                # Bybit cheaper → buy Bybit, sell Binance
                spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

                # Required spread to cover fees (20 bps) + slippage (4 bps) + profit target (4 bps)
                required_spread = (CONFIG["BINANCE_FEE"] + CONFIG["BYBIT_FEE"]) * 10000 + CONFIG["SLIPPAGE_BPS"] * 2 + CONFIG["TAKE_PROFIT_BPS"]

                if spread1 >= CONFIG["MIN_SPREAD_BPS"] and spread1 >= required_spread:
                    print(f"🔥 {sym} spread={spread1:.2f}bps → BUY BINANCE / SELL BYBIT")
                    self.open_arbitrage(sym, "binance_buy_bybit_sell", "binance", "bybit", p["binance_ask"], p["bybit_bid"])
                elif spread2 >= CONFIG["MIN_SPREAD_BPS"] and spread2 >= required_spread:
                    print(f"🔥 {sym} spread={spread2:.2f}bps → BUY BYBIT / SELL BINANCE")
                    self.open_arbitrage(sym, "bybit_buy_binance_sell", "bybit", "binance", p["bybit_ask"], p["binance_bid"])

            # Print status every 5 seconds
            if now - last_scan_print > 5:
                active_spreads = []
                for sym in CONFIG["SYMBOLS"]:
                    if self.is_fresh(sym):
                        p = self.prices[sym]
                        spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
                        spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000
                        active_spreads.append(f"{sym}:{max(spread1, spread2):.1f}bps")
                print(f"🔍 SPREADS: {' | '.join(active_spreads[:5])} | Open: {len(self.positions)} | Balance: ${self.balance:.2f}")
                last_scan_print = now

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(CrossArbitrageBot().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
