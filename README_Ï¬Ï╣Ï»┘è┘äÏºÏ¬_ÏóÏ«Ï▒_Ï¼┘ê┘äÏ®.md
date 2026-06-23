# 📦 تعديلات آخر جولة — دمج رسائل أحداث الصفقة المتزامنة

> ⚠️ تُطبَّق **فوق** الحزم السابقة. تعتمد على ملفات الجولات السابقة.

## 🎯 المشكلة
وصلتك **رسالتان لنفس الصفقة بنفس الوقت** (`Long-running Trade` و`Exit / Risk Warning`).
السبب: عند وقوع أكثر من حدث في نفس دورة التقييم، كان الكود يرسل **رسالة Telegram منفصلة
لكل حدث** — فظهرت رسالتان متطابقتان تقريباً في نفس اللحظة.

## ✅ الحل
دمج كل أحداث الصفقة في **رسالة واحدة** لكل صفقة في الدورة:
- العنوان = الحدث الأهم (حسب أولوية: TP2 > SL > تريلينغ > تعادل > TP1 > … > EXIT_WARNING > LONG_RUNNING).
- ملاحظة مختصرة لكل حدث ضمن نفس الرسالة (دون تكرار).
- بيانات الصفقة (الدخول/الوقف/الأهداف/الربح) تظهر مرة واحدة.

### مثال (بدل رسالتين → رسالة واحدة)
```
⚠️ Exit / Risk Warning - XAU/USD
━━━━━━━━━━━━━━━━━━━━━
🆔 ID: TRADE_2026...7cf3f415
📊 Type: SELL
📍 Entry: 4124.82
🛑 Stop Loss: 4144.82
🎯 TP1: 4098.15   🎯 TP2: 4078.15
💰 Current Price: 4136.12
📈 Current PnL: -113.0 pts ❌
📌 Status: OPEN → OPEN
📊 Progress to TP1: 0%   ⏱ Time open: 4.4h

• ⚠️ Exit/risk warning: trade is near a danger zone or adverse move is deep.
• ⏱ Trade has been open for a long time. Monitor momentum and news risk.

⚠️ Educational paper-trading update only. Not financial advice.
```

## 📂 الملفات (3)
- `services/telegram_bot.py` — دالة `send_trade_events()` الجديدة (رسالة مدموجة).
- `agents/open_trades_manager.py` — استدعاء رسالة واحدة لكل صفقة بدل حلقة لكل حدث.
- `tests/test_trade_event_dedup.py` — 5 اختبارات جديدة.

## 🚀 الرفع
استبدل الملفات الـ3 فوق نظيراتها، أو:
```bash
cd Nabil-gold
git apply event-dedup-changes.patch
python -m pytest tests/ -q     # المتوقع: 291 passed
```
