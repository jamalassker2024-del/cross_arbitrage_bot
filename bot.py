import asyncio, json, os, websockets
from decimal import Decimal
import time

# --- CONFIGURATION (إعدادات صارمة لوقف النزيف) ---
SYMBOL = os.getenv("SYMBOL", "btcusdt")
TRADE_SIZE = Decimal("1000")
ENTRY_THRESHOLD = Decimal("0.45") # رفعنا القوة المطلوبة لـ 45% (جدار حقيقي فقط)
FEE_RATE = Decimal("0.00075")     # رسوم واقعية
TARGET_PROFIT = Decimal("0.0040") # 0.4% ربح (ليكون الربح حقيقي بعد الرسوم)
STOP_LOSS = Decimal("-0.0035")
COOLDOWN_TIME = 60 # صمت لمدة دقيقة بعد كل صفقة

class SentinelV4:
    def __init__(self):
        self.balance = Decimal("5000.00")
        self.active_trade = None
        self.last_trade_time = 0

    def get_deep_imb(self, bids, asks):
        # تحليل أعمق (20 مستوى) للتأكد من أن السيولة حقيقية وليست وهمية
        b_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids[:15])
        a_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks[:15])
        return (b_w - a_w) / (b_w + a_w)

    async def start(self):
        url = f"wss://stream.binance.com:9443/ws/{SYMBOL}@depth20@100ms"
        print(f"🛡️ Sentinel V4: PROTECT MODE ACTIVE")

        async with websockets.connect(url) as ws:
            while True:
                try:
                    data = json.loads(await ws.recv())
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    curr_imb = self.get_deep_imb(bids, asks)

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        gross_roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        
                        net_pnl = (TRADE_SIZE * gross_roi) - (TRADE_SIZE * FEE_RATE * 2)

                        # منع الخروج المتسرع: اخرج فقط عند الهدف أو الاستوب لوس الحقيقي
                        if (net_pnl / TRADE_SIZE) >= TARGET_PROFIT or (net_pnl / TRADE_SIZE) <= STOP_LOSS:
                            self.balance += net_pnl
                            self.last_trade_time = time.time()
                            status = "💰 PROFIT" if net_pnl > 0 else "🛡️ STOP-LOSS"
                            print(f"{status} | Net: ${round(net_pnl, 2)} | Bal: ${round(self.balance, 2)}")
                            self.active_trade = None
                    else:
                        # شرط الهدوء + شرط قوة الجدار
                        if time.time() - self.last_trade_time > COOLDOWN_TIME:
                            if curr_imb > ENTRY_THRESHOLD:
                                self.active_trade = {"side": "BUY", "entry": ask_p}
                                print(f"⚡ SNIPER LONG @ {ask_p} | Imb: {round(curr_imb, 2)}")
                            elif curr_imb < -ENTRY_THRESHOLD:
                                self.active_trade = {"side": "SELL", "entry": bid_p}
                                print(f"⚡ SNIPER SHORT @ {bid_p} | Imb: {round(curr_imb, 2)}")

                except Exception:
                    continue

if __name__ == "__main__":
    asyncio.run(SentinelV4().start())
