import asyncio
import json
import os
import websockets
from decimal import Decimal
from collections import deque

# --- CONFIGURATION (إعدادات السرعة القصوى) ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal("1000") 
ENTRY_THRESHOLD = Decimal("0.18")    # تقليل العتبة للدخول أسرع (كانت 0.25)
VELOCITY_THRESHOLD = Decimal("0.02") # تقليل شرط التسارع (كان 0.05)
FEE = Decimal("0.0004")
TARGET_PROFIT = Decimal("0.0015")   # هدف ربح 0.15% (لتحقيق صفقات خاطفة وأكثر تكراراً)
STOP_LOSS = Decimal("-0.0035")

class SentinelHyperFast:
    def __init__(self):
        self.balance = Decimal("5000.00")
        self.active_trade = None
        # تقليل الذاكرة لسرعة الحساب
        self.imb_history = deque(maxlen=2) 

    def get_weighted_imb(self, bids, asks):
        # التركيز فقط على أول 5 مستويات (أسرع وأدق للسكالبينج)
        b_w = sum((Decimal(b[0]) * Decimal(b[1])) * (Decimal(1) / (i + 1)) for i, b in enumerate(bids[:5]))
        a_w = sum((Decimal(a[0]) * Decimal(a[1])) * (Decimal(1) / (i + 1)) for i, a in enumerate(asks[:5]))
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        # استخدام f-stream للسرعة القصوى
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"
        
        async with websockets.connect(url) as ws:
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_weighted_imb(bids, asks)
                    
                    # حساب السرعة اللحظية
                    velocity = curr_imb - (self.imb_history[0] if self.imb_history else curr_imb)
                    self.imb_history.append(curr_imb)

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        net_roi = roi - (FEE * 2)

                        # إغلاق فوري عند الربح أو إذا انقلب الـ Imbalance ضدنا
                        imb_flipped = (t['side'] == "BUY" and curr_imb < -0.1) or (t['side'] == "SELL" and curr_imb > 0.1)

                        if net_roi >= TARGET_PROFIT or net_roi <= STOP_LOSS or (net_roi > 0 and imb_flipped):
                            pnl = TRADE_SIZE * net_roi
                            self.balance += pnl
                            print(f"⚡ FAST EXIT | PnL: ${round(pnl, 2)} | Bal: ${round(self.balance, 2)}")
                            self.active_trade = None
                    
                    else:
                        # الدخول فوراً عند توفر السيولة
                        if curr_imb > ENTRY_THRESHOLD and velocity > VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "BUY", "entry": ask_p}
                            print(f"🚀 SNIPED LONG @ {ask_p}")
                        elif curr_imb < -ENTRY_THRESHOLD and velocity < -VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "SELL", "entry": bid_p}
                            print(f"🚀 SNIPED SHORT @ {bid_p}")

                except Exception:
                    await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(SentinelHyperFast().start())
