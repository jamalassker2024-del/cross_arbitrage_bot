#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import websockets
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["btcusdt", "ethusdt", "solusdt", "pepeusdt", "dogeusdt"],
    "ORDER_SIZE_USDT": Decimal("10.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "MIN_SPREAD_BPS": Decimal("18"),
    "TOTAL_FEES_BPS": Decimal("15.5"),
    "COOLDOWN_SEC": 0.5, 
    "BINANCE_SPOT_WS": "wss://stream.binance.com:9443/ws",
    "BYBIT_LINEAR_WS": "wss://stream.bybit.com/v5/public/linear",
}

class AtomicArbitrageMachine:
    def __init__(self):
        # Initialize with None to strictly verify data presence
        self.prices = {s: {
            "binance_ask": None, "binance_bid": None,
            "bybit_ask": None, "bybit_bid": None
        } for s in CONFIG["SYMBOLS"]}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.total_profit = Decimal('0')
        self.last_trade_time = {}
        self.start_time = time.time()

    async def binance_handler(self, symbol):
        url = f"{CONFIG['BINANCE_SPOT_WS']}/{symbol.lower()}@bookTicker"
        async with websockets.connect(url) as ws:
            async for msg in ws:
                data = json.loads(msg)
                self.prices[symbol]["binance_ask"] = Decimal(data['a'])
                self.prices[symbol]["binance_bid"] = Decimal(data['b'])
                await self.check_arbitrage(symbol)

    async def bybit_handler(self, symbol):
        async with websockets.connect(CONFIG['BYBIT_LINEAR_WS']) as ws:
            # Bybit V5 requires a ping to keep connection alive
            await ws.send(json.dumps({"op": "subscribe", "args": [f"orderbook.1.{symbol.upper()}"]}))
            async for msg in ws:
                data = json.loads(msg)
                if 'data' in data:
                    d = data['data']
                    # FIX: Update only what is provided, keep the other side 'sticky'
                    if 'a' in d and d['a']:
                        self.prices[symbol]["bybit_ask"] = Decimal(d['a'][0][0])
                    if 'b' in d and d['b']:
                        self.prices[symbol]["bybit_bid"] = Decimal(d['b'][0][0])
                    
                    await self.check_arbitrage(symbol)

    async def check_arbitrage(self, symbol):
        p = self.prices[symbol]
        
        # Ensure all 4 price points exist before calculating
        if any(v is None for v in p.values()):
            return
        
        # Cooldown
        if symbol in self.last_trade_time and time.time() - self.last_trade_time[symbol] < CONFIG["COOLDOWN_SEC"]:
            return

        # Buy Binance @ Ask, Sell Bybit @ Bid
        spread1 = (p["bybit_bid"] - p["binance_ask"]) / p["binance_ask"] * 10000
        # Buy Bybit @ Ask, Sell Binance @ Bid
        spread2 = (p["binance_bid"] - p["bybit_ask"]) / p["bybit_ask"] * 10000

        if spread1 > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute(symbol, "BIN -> BYB", p["binance_ask"], p["bybit_bid"], spread1)
        elif spread2 > CONFIG["MIN_SPREAD_BPS"]:
            await self.execute(symbol, "BYB -> BIN", p["bybit_ask"], p["binance_bid"], spread2)

    async def execute(self, symbol, label, b_p, s_p, spread):
        net_bps = spread - CONFIG["TOTAL_FEES_BPS"]
        profit = (CONFIG["ORDER_SIZE_USDT"] * net_bps) / 10000
        
        if profit > 0:
            self.balance += profit
            self.total_trades += 1
            self.total_profit += profit
            self.last_trade_time[symbol] = time.time()
            print(f"💰 [ARB] {symbol.upper()} | {label} | Net: +${profit:.4f} | Spread: {spread:.1f}bps | Bal: ${self.balance:.2f}")

    async def run(self):
        print(f"🚀 Starting Atomic Arb on {CONFIG['SYMBOLS']}...")
        tasks = [self.binance_handler(s) for s in CONFIG["SYMBOLS"]] + \
                [self.bybit_handler(s) for s in CONFIG["SYMBOLS"]]
        
        # Add a heartbeat so you know the bot is alive
        async def heartbeat():
            while True:
                await asyncio.sleep(15)
                print(f"💓 Heartbeat: {self.total_trades} trades executed. Runtime: {int(time.time()-self.start_time)}s")
        
        await asyncio.gather(*tasks, heartbeat())

if __name__ == "__main__":
    asyncio.run(AtomicArbitrageMachine().run())
