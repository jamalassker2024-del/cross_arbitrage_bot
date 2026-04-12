import asyncio
import json
import os
import websockets
from decimal import Decimal
from collections import deque

# --- CONFIGURATION (إعدادات هجومية لفتح صفقات أكثر) ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal("1000") 
# خفضنا العتبة ليكون البوت حساساً لأي سيولة
ENTRY_THRESHOLD = Decimal("0.12")    
# جعلنا شرط التسارع بسيطاً جداً لعدم عرقلة الصفقات
VELOCITY_THRESHOLD = Decimal("0.005") 
FEE = Decimal("0.0004")
TARGET_PROFIT = Decimal("0.0012")   # 0.12% ربح خاطف
STOP_LOSS = Decimal("-0.0030")

class SentinelAggressive:
    def __init__(self):
        self.balance = Decimal("5000.00")
        self.active_trade = None
        self.imb_history = deque(maxlen=5) 

    def get_weighted_imb(self, bids, asks):
        # نركز على أول 10 مستويات لضمان رؤية "الجدران" الحقيقية
        b_w = sum((Decimal(b[0]) * Decimal(b[1])) * (Decimal(1) / (i + 1)) for i, b in enumerate(bids[:10]))
        a_w = sum((Decimal(a[0]) * Decimal(a[1])) * (Decimal(1) / (i + 1)) for i, a in enumerate(asks[:10]))
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"
        print(f"🔥 Sentinel Aggressive Started | Monitoring {SYMBOL.upper()}")
        
        async with websockets.connect(url) as ws:
            while True:
                try:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_weighted_imb(bids, asks)
                    
                    # حساب السرعة بناءً على متوسط آخر 5 قراءات ليكون أدق
                    avg_prev_imb = sum(self.imb_history) / len(self.imb_history) if self.imb_history else curr_imb
                    velocity = curr_imb - avg_prev_imb
                    self.imb_history.append(curr_imb)

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        net_roi = roi - (FEE * 2)

                        # إغلاق سريع جداً عند الربح أو انعكاس السيولة
                        imb_flipped = (t['side'] == "BUY" and curr_imb < -0.05) or (t['side'] == "SELL" and curr_imb > 0.05)

                        if net_roi >= TARGET_PROFIT or net_roi <= STOP_LOSS or (net_roi > 0 and imb_flipped):
                            pnl = TRADE_SIZE * net_roi
                            self.balance += pnl
                            print(f"💰 EXIT | PnL: ${round(pnl, 2)} | Bal: ${round(self.balance, 2)} | Reason: {'TP/SL' if net_roi >= TARGET_PROFIT or net_roi <= STOP_LOSS else 'FLIP'}")
                            self.active_trade = None
                    
                    else:
                        # شروط دخول هجومية: يكفي وجود اختلال واضح (Imbalance) مع حركة بسيطة
                        if curr_imb > ENTRY_THRESHOLD and velocity > VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "BUY", "entry": ask_p}
                            print(f"🚀 SNIPE LONG @ {ask_p} | Imb: {round(curr_imb, 2)}")
                        elif curr_imb < -ENTRY_THRESHOLD and velocity < -VELOCITY_THRESHOLD:
                            self.active_trade = {"side": "SELL", "entry": bid_p}
                            print(f"🚀 SNIPE SHORT @ {bid_p} | Imb: {round(curr_imb, 2)}")

                except Exception as e:
                    # في حال حدوث خطأ، لا يتوقف البوت بل يحاول مجدداً
                    await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(SentinelAggressive().start())
