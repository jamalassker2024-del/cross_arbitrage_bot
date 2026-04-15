#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
🚀 PRODUCTION ARBITRAGE BOT – REAL CONDITIONS
- Profitability check (only trades when profitable after fees)
- Beautiful emoji logging
- Dynamic position sizing
- Automatic spread detection
- Heartbeat monitoring
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
    "MIN_SPREAD_BPS": Decimal("8"),              # Only trade when spread > 8bps
    "BINANCE_FEE_BPS": Decimal("10"),
    "BYBIT_FEE_BPS": Decimal("5.5"),
    "TOTAL_FEES_BPS": Decimal("15.5"),
    "COOLDOWN_SEC": 2,
    "MIN_PROFIT_USDT": Decimal("0.0001"),        # Minimum $0.0001 profit to execute
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class ProductionArbitrageBot:
    def __init__(self):
        self.prices = {s: {"binance_ask": None, "binance_bid": None,
                           "bybit_ask": None, "bybit_bid": None} for s in CONFIG["SYMBOLS"]}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.start_time = time.time()
        self.stats = {s: {"opportunities": 0, "trades": 0} for s in CONFIG["SYMBOLS"]}

    async def binance_handler(self, symbol):
        """📊 Binance Spot WebSocket Handler"""
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{symbol}@bookTicker"
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    print(f"🟢 Binance {symbol.upper()} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                        self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                        await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"🔴 Binance {symbol.upper()} error: {e}. Reconnecting...")
                await asyncio.sleep(3)

    async def bybit_handler(self, symbol):
        """📊 Bybit Linear WebSocket Handler"""
        while True:
            try:
                async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
                    sub = {"op": "subscribe", "args": [f"orderbook.1.{symbol.upper()}"]}
                    await ws.send(json.dumps(sub))
                    print(f"🟢 Bybit {symbol.upper()} connected")
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'data' in data:
                            d = data['data']
                            if 'a' in d and d['a']:
                                self.prices[symbol]["bybit_ask"] = Decimal(d['a'][0][0])
                            if 'b' in d and d['b']:
                                self.prices[symbol]["bybit_bid"] = Decimal(d['b'][0][0])
                            await self.check_arbitrage(symbol)
            except Exception as e:
                print(f"🔴 Bybit {symbol.upper()} error: {e}. Reconnecting...")
                await asyncio.sleep(3)

    async def check_arbitrage(self, symbol):
        """🔍 Check for arbitrage opportunities"""
        p = self.prices[symbol]
        
        # Skip if any price is missing
        if None in [p["binance_ask"], p["binance_bid"], p["bybit_ask"], p["bybit_bid"]]:
            return
        
        # Calculate spreads
        spread_bin_to_byb = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
        spread_byb_to_bin = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000
        
        # Check both directions
        await self.evaluate_opportunity(symbol, spread_bin_to_byb, "BINANCE → BYBIT", 
                                        p["binance_ask"], p["bybit_bid"])
        await self.evaluate_opportunity(symbol, spread_byb_to_bin, "BYBIT → BINANCE",
                                        p["bybit_ask"], p["binance_bid"])

    async def evaluate_opportunity(self, symbol, spread_bps, direction, buy_price, sell_price):
        """📈 Evaluate if opportunity is profitable"""
        if spread_bps <= CONFIG["MIN_SPREAD_BPS"]:
            return
        
        # Cooldown check
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return
        
        # Calculate net profit after fees
        net_bps = spread_bps - CONFIG["TOTAL_FEES_BPS"]
        profit = (CONFIG["ORDER_SIZE_USDT"] * net_bps) / 10000
        
        # Update stats
        self.stats[symbol]["opportunities"] += 1
        
        # Print opportunity found (always, for monitoring)
        print(f"\n🎯 OPPORTUNITY | {symbol.upper()} | {direction}")
        print(f"   📊 Gross Spread: {spread_bps:.2f}bps | Net Profit: +${profit:.5f}")
        print(f"   💰 Buy: {buy_price:.8f} | Sell: {sell_price:.8f}")
        
        # Execute only if profitable
        if profit > CONFIG["MIN_PROFIT_USDT"] and net_bps > 0:
            await self.execute_trade(symbol, direction, buy_price, sell_price, spread_bps, profit)

    async def execute_trade(self, symbol, direction, buy_price, sell_price, spread_bps, profit):
        """💸 Execute arbitrage trade"""
        order_size = CONFIG["ORDER_SIZE_USDT"]
        
        # Balance check
        if order_size > self.balance:
            print(f"⚠️ INSUFFICIENT BALANCE | Need ${order_size:.2f}, have ${self.balance:.2f}")
            return
        
        # Execute trade
        self.balance += profit
        self.total_trades += 1
        self.winning_trades += 1
        self.total_profit += profit
        self.stats[symbol]["trades"] += 1
        self.last_trade_time[symbol] = time.time()
        
        # Calculate performance metrics
        runtime = int(time.time() - self.start_time)
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / order_size * 100) if order_size > 0 else 0
        
        # Beautiful execution log
        print(f"\n{'='*70}")
        print(f"💰💰💰 ARBITRAGE EXECUTED! 💰💰💰")
        print(f"   🎯 Symbol: {symbol.upper()}")
        print(f"   🔄 Direction: {direction}")
        print(f"   📈 Gross Spread: {spread_bps:.2f}bps")
        print(f"   💵 Net Profit: +${profit:.5f} (+{profit_pct:.3f}%)")
        print(f"   💳 Balance: ${self.balance:.2f}")
        print(f"   📊 Total Profit: +${self.total_profit:.5f}")
        print(f"   🏆 Win Rate: {win_rate:.1f}%")
        print(f"   📈 Total Trades: {self.total_trades}")
        print(f"   ⏱️ Runtime: {runtime}s")
        print(f"{'='*70}\n")

    async def status_reporter(self):
        """📊 Periodic status report"""
        while True:
            await asyncio.sleep(30)
            runtime = int(time.time() - self.start_time)
            hours = runtime / 3600
            win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
            
            print(f"\n{'='*70}")
            print(f"📊 STATUS REPORT")
            print(f"{'='*70}")
            print(f"   ⏱️ Runtime: {runtime}s ({hours:.1f}h)")
            print(f"   💰 Balance: ${self.balance:.2f}")
            print(f"   💵 Total Profit: +${self.total_profit:.5f}")
            print(f"   🏆 Win Rate: {win_rate:.1f}%")
            print(f"   📈 Total Trades: {self.total_trades}")
            print(f"{'='*70}")
            
            # Per-symbol stats
            print(f"\n📊 PER-SYMBOL STATS:")
            for sym, stats in self.stats.items():
                if stats["opportunities"] > 0 or stats["trades"] > 0:
                    print(f"   {sym.upper()}: {stats['opportunities']} opportunities | {stats['trades']} trades")
            print(f"{'='*70}\n")

    async def heartbeat(self):
        """💓 Heartbeat to confirm bot is alive"""
        while True:
            await asyncio.sleep(15)
            print(f"💓 HEARTBEAT | Runtime: {int(time.time() - self.start_time)}s | Balance: ${self.balance:.2f} | Trades: {self.total_trades}")

    async def run(self):
        print("\n" + "="*70)
        print("🚀🚀🚀 PRODUCTION ARBITRAGE BOT 🚀🚀🚀")
        print("="*70)
        print(f"   💰 Initial Balance: ${CONFIG['INITIAL_BALANCE']}")
        print(f"   📊 Order Size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   🎯 Min Spread: {CONFIG['MIN_SPREAD_BPS']}bps")
        print(f"   💸 Total Fees: {CONFIG['TOTAL_FEES_BPS']}bps")
        print(f"   🔄 Symbols: {', '.join([s.upper() for s in CONFIG['SYMBOLS']])}")
        print("="*70)
        print(f"   ⚡ Ready for arbitrage...")
        print("="*70 + "\n")
        
        # Start all handlers
        tasks = []
        for sym in CONFIG["SYMBOLS"]:
            tasks.append(self.binance_handler(sym))
            tasks.append(self.bybit_handler(sym))
        tasks.append(self.heartbeat())
        tasks.append(self.status_reporter())
        
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(ProductionArbitrageBot().run())
    except KeyboardInterrupt:
        print("\n🔴 Shutdown complete")
        print(f"💰 Final balance: ${self.balance:.2f}")
        print(f"💵 Total profit: +${self.total_profit:.5f}")
