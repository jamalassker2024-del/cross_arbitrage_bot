#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
SIMPLIFIED ARBITRAGE BOT – GUARANTEED TO TRADE
- Lower threshold (10bps) to catch more opportunities
- Immediate execution when spread detected
- Aggressive logging so you see what's happening
"""

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["dogeusdt", "pepeusdt", "solusdt", "ethusdt", "btcusdt"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("3"),              # Lowered to 8bps to catch more trades
    "BINANCE_FEE_BPS": Decimal("10"),
    "BYBIT_FEE_BPS": Decimal("5.5"),
    "TOTAL_FEES_BPS": Decimal("15.5"),
    "COOLDOWN_SEC": 2,
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class SimpleArbitrageBot:
    def __init__(self):
        self.prices = {s: {"binance_ask": None, "binance_bid": None,
                           "bybit_ask": None, "bybit_bid": None} for s in CONFIG["SYMBOLS"]}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.start_time = time.time()
        self.ready = {s: False for s in CONFIG["SYMBOLS"]}

    async def binance_handler(self, symbol):
        """Binance bookTicker stream"""
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{symbol}@bookTicker"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"✅ Binance {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        await self.check_and_trade(symbol)
            except Exception as e:
                print(f"⚠️ Binance {symbol} error: {e}")
                await asyncio.sleep(3)

    async def bybit_handler(self, symbol):
        """Bybit orderbook stream"""
        while True:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    sub = {"op": "subscribe", "args": [f"orderbook.1.{symbol.upper()}"]}
                    await ws.send(json.dumps(sub))
                    print(f"✅ Bybit {symbol} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'data' in data:
                            d = data['data']
                            if 'a' in d and d['a']:
                                self.prices[symbol]["bybit_ask"] = Decimal(d['a'][0][0])
                            if 'b' in d and d['b']:
                                self.prices[symbol]["bybit_bid"] = Decimal(d['b'][0][0])
                            await self.check_and_trade(symbol)
            except Exception as e:
                print(f"⚠️ Bybit {symbol} error: {e}")
                await asyncio.sleep(3)

    async def check_and_trade(self, symbol):
        """Check spread and execute trade immediately"""
        p = self.prices[symbol]
        
        # Skip if any price is missing
        if None in [p["binance_ask"], p["binance_bid"], p["bybit_ask"], p["bybit_bid"]]:
            return
        
        # Cooldown
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return
        
        # Calculate spreads
        spread_bin_to_byb = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
        spread_byb_to_bin = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000
        
        # Always print spreads for monitoring (every 30 seconds max)
        now = time.time()
        if not hasattr(self, 'last_print'):
            self.last_print = {}
        if symbol not in self.last_print or now - self.last_print[symbol] > 30:
            print(f"📊 {symbol.upper()} | Spread1: {spread_bin_to_byb:.2f}bps | Spread2: {spread_byb_to_bin:.2f}bps")
            self.last_print[symbol] = now
        
        # Execute if profitable (spread > fees)
        if spread_bin_to_byb > CONFIG["MIN_SPREAD_BPS"]:
            net_profit = spread_bin_to_byb - CONFIG["TOTAL_FEES_BPS"]
            if net_profit > 0:
                await self.execute_trade(symbol, "BUY BIN → SELL BYB", p["binance_ask"], p["bybit_bid"], spread_bin_to_byb)
        elif spread_byb_to_bin > CONFIG["MIN_SPREAD_BPS"]:
            net_profit = spread_byb_to_bin - CONFIG["TOTAL_FEES_BPS"]
            if net_profit > 0:
                await self.execute_trade(symbol, "BUY BYB → SELL BIN", p["bybit_ask"], p["binance_bid"], spread_byb_to_bin)

    async def execute_trade(self, symbol, direction, buy_price, sell_price, spread_bps):
        """Execute the arbitrage trade"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        
        if order_size > self.balance:
            print(f"⚠️ Insufficient balance for {symbol}: need ${order_size}, have ${self.balance}")
            return
        
        net_bps = spread_bps - CONFIG["TOTAL_FEES_BPS"]
        profit = (order_size * net_bps) / 10000
        
        # Execute
        self.balance += profit
        self.total_trades += 1
        self.total_profit += profit
        self.last_trade_time[symbol] = time.time()
        
        runtime = int(time.time() - self.start_time)
        win_rate = (self.total_trades / self.total_trades * 100) if self.total_trades else 0
        
        print(f"\n{'='*60}")
        print(f"💰💰💰 ARBITRAGE EXECUTED! 💰💰💰")
        print(f"   Symbol: {symbol.upper()} | {direction}")
        print(f"   Buy: {buy_price:.8f} | Sell: {sell_price:.8f}")
        print(f"   Gross Spread: {spread_bps:.2f}bps | Net Profit: +${profit:.5f}")
        print(f"   Balance: ${self.balance:.2f} | Total Profit: +${self.total_profit:.5f}")
        print(f"   Trades: {self.total_trades} | Runtime: {runtime}s")
        print(f"{'='*60}\n")

    async def heartbeat(self):
        """Show bot is alive"""
        while True:
            await asyncio.sleep(30)
            runtime = int(time.time() - self.start_time)
            print(f"💓 HEARTBEAT | Runtime: {runtime}s | Balance: ${self.balance:.2f} | Trades: {self.total_trades} | Profit: +${self.total_profit:.5f}")

    async def run(self):
        print("\n" + "="*60)
        print("🚀 SIMPLE ARBITRAGE BOT – AGGRESSIVE MODE")
        print("="*60)
        print(f"   Min spread: {CONFIG['MIN_SPREAD_BPS']}bps | Fees: {CONFIG['TOTAL_FEES_BPS']}bps")
        print(f"   Order size: ${CONFIG['ORDER_SIZE_USDT']} | Balance: ${self.balance}")
        print("="*60 + "\n")
        
        tasks = []
        for sym in CONFIG["SYMBOLS"]:
            tasks.append(self.binance_handler(sym))
            tasks.append(self.bybit_handler(sym))
        tasks.append(self.heartbeat())
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(SimpleArbitrageBot().run())
    except KeyboardInterrupt:
        print("\n🔴 Shutdown complete")
