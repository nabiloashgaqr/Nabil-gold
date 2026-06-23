# 📦 تعديلات آخر جولة — دمج رسائل نهاية اليوم في رسالة واحدة

> ⚠️ تُطبَّق **فوق** الحزم السابقة. تعتمد على ملفات الجولات السابقة.

## 🎯 المشكلة
في نهاية اليوم كان الـworkflow يرسل **4 رسائل منفصلة على الأقل** خلال دقيقة:
1. تحديث الصفقات (+ رسائل أحداث TP/SL لكل صفقة)
2. ملخّص التعلّم
3. مراجعة AI للصفقات الخاسرة
4. التقرير اليومي

= إزعاج وكثرة رسائل.

## ✅ الحل: رسالة واحدة مدمجة (Digest)
السكربتات الثلاثة (تحديث الصفقات / التعلّم / المراجعة) تعمل الآن في **وضع كتم**
(`EOD_QUIET=true`):
- **لا ترسل رسائل Telegram خاصة بها** — لكنها تُحدّث قاعدة البيانات كالمعتاد.
- التعلّم والمراجعة يكتبان ملخّصهما في `storage/eod_*.txt`.
- **التقرير اليومي** يجمع كل شيء (الأداء + الصفقات المفتوحة + التعلّم + المراجعة)
  في **رسالة واحدة منظّمة بأقسام**، ثم يحذف الملفات المؤقتة.

أحداث الصفقات (TP/SL) أثناء تحديث نهاية اليوم تُكتم أيضاً وتُلخَّص ضمن التقرير.

## 📊 الشكل النهائي (رسالة واحدة)
```
📊 Gold AI Signals — Daily Summary
━━━━━━━━━━━━━━━━━━━━━
📅 2026-06-23 (Asia/Hebron)

📈 Performance
Trades: 1 | Wins: 0 | Losses: 1
PnL: -112 pts

🔄 Open Trades
• Count: 1
• SELL @ 4124.82 → 4136.00 (-11.2)
• Est. Total PnL: -11.2 pts

🧠 Learning Update
Trades analyzed: 12 | Win rate: 58%
Top agent: SMC (+0.05) | Weakest: Classical (-0.03)

🔎 AI Trade Review
Reviewed: 2 trade(s)
🔻 Trade: TRADE_X
├ Category: ENTRY_TIMING
└ Review confidence: 80%

⚠️ Paper-trading only • Educational
━━━━━━━━━━━━━━━━━━━━━
```

## 📂 الملفات (6)
- `scripts/run_learning.py` — وضع الكتم + كتابة الملخّص لملف.
- `scripts/run_trade_review.py` — وضع الكتم + كتابة الملخّص لملف.
- `scripts/run_trade_updates.py` — كتم الرسائل في نهاية اليوم.
- `scripts/run_daily_report.py` — دمج كل الأقسام في رسالة واحدة + تنظيف.
- `.github/workflows/daily_report.yml` — `EOD_QUIET=true` للخطوات الثلاث.
- `tests/test_eod_consolidation.py` — 5 اختبارات جديدة.

## ℹ️ ملاحظات
- خارج نهاية اليوم، تشغيل أي سكربت بمفرده يدوياً **يرسل رسالته كالمعتاد** (الكتم
  فقط عند `EOD_QUIET=true`).
- حدّ Telegram 4096 حرف؛ الرسالة تُقصّ بأمان إن تجاوزت 3900.

## 🚀 الرفع
استبدل الملفات الـ6 فوق نظيراتها، أو:
```bash
cd Nabil-gold
git apply eod-digest-changes.patch
python -m pytest tests/ -q     # المتوقع: 296 passed
```
