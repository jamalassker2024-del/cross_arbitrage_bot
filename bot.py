import asyncio, json, os, websockets
from decimal import Decimal

# --- CONFIGURATION (إعدادات توربو) ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal("1000")
ENTRY_THRESHOLD = Decimal("0.08") # حساس جداً لأي ميل في الكفة
FEE = Decimal("0.0004")
TARGET_PROFIT = Decimal("0.0010") # 0.1% هدف قريب جداً للخطف
STOP_LOSS = Decimal("-0.0025")

class SentinelOverdrive:
    def __init__(self):
        self.balance = Decimal("5000.00")
        self.active_trade = None

    def get_quick_imb(self, bids, asks):
        # حساب فوري لأول 5 مستويات - هذا هو ما تراه عينك في واجهة بينانس
        b_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids[:5])
        a_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks[:5])
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        # استخدام الرابط المباشر للبيانات الخام
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth5@100ms"
        print(f"⚡ OVERDRIVE ACTIVE | {SYMBOL.upper()}")

        async with websockets.connect(url) as ws:
            while True:
                try:
                    data = json.loads(await ws.recv())
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_quick_imb(bids, asks)

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        
                        # إغلاق فوري إذا بدأ الدفتر يتغير (حتى قبل الوصول للهدف)
                        imb_flip = (t['side'] == "BUY" and curr_imb < 0) or (t['side'] == "SELL" and curr_imb > 0)
                        
                        if roi - (FEE * 2) >= TARGET_PROFIT or roi - (FEE * 2) <= STOP_LOSS or imb_flip:
                            pnl = TRADE_SIZE * (roi - (FEE * 2))
                            self.balance += pnl
                            print(f"🏁 EXIT | PnL: ${round(pnl, 2)} | Bal: ${round(self.balance, 2)}")
                            self.active_trade = None
                    else:
                        # الدخول فوراً بناءً على ميل الكفة الحالي فقط
                        if curr_imb > ENTRY_THRESHOLD:
                            self.active_trade = {"side": "BUY", "entry": ask_p}
                            print(f"🎯 LONG @ {ask_p} | Imb: {round(curr_imb, 2)}")
                        elif curr_imb < -ENTRY_THRESHOLD:
                            self.active_trade = {"side": "SELL", "entry": bid_p}
                            print(f"🎯 SHORT @ {bid_p} | Imb: {round(curr_imb, 2)}")

                except Exception:
                    continue

if __name__ == "__main__":
    asyncio.run(SentinelOverdrive().start())
