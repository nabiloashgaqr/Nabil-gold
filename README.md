# 🤖 أوامر Telegram التفاعلية — حزمة الملفات

البوت كان يُرسل فقط. الآن يستقبل أوامر المستخدم ويردّ عليها.

## ✅ الأوامر
| الأمر | الرد |
|---|---|
| `/status` | السعر الحالي + آخر إشارة (اتجاه/سعر/حالة/ثقة) |
| `/open` | الصفقات المفتوحة + المعلّقة + صافي الربح العائم |
| `/today` | أداء اليوم (رابح/خاسر/Net/PF) |
| `/stats` | الأداء العام (آخر 100 صفقة) |
| `/price` | سعر الذهب الحالي فقط |
| `/rules` | آخر قواعد التعلّم |
| `/help` `/start` | قائمة الأوامر |

## 🧩 كيف يعمل (مهم)
المشروع على GitHub Actions (بلا سيرفر دائم)، فالأوامر تُعالَج بـ**polling**:
- Workflow `telegram_commands.yml` يعمل **كل دقيقة**، يجلب الأوامر الجديدة ويرد.
- الردود تصل خلال **~1–2 دقيقة** (ليست لحظية، لكنها كافية لبوت إشارات).
- يُحفظ آخر تحديث مُعالَج في `storage/telegram_offset.json` فلا يتكرّر.

## 📂 الملفات
```
files/services/telegram_bot.py          ← get_updates() + reply() (مُحدّث)
files/services/telegram_commands.py     ← معالج الأوامر (جديد)
files/scripts/run_telegram_commands.py  ← السكربت (جديد)
files/.github/workflows/telegram_commands.yml ← polling كل دقيقة (جديد)
files/tests/test_telegram_commands.py   ← 10 اختبارات (جديد)
```

## 🚀 الإعداد
1. انسخ مجلد `files/` فوق مستودعك.
2. **في BotFather** (مهم للمجموعات): أرسل `/setprivacy` → اختر البوت → **Disable**
   (حتى يقرأ البوت الأوامر في المجموعات/القنوات). في المحادثة الخاصة يعمل دائماً.
3. الأسرار المستخدمة موجودة أصلاً: `TELEGRAM_BOT_TOKEN`, `SUPABASE_URL/KEY`, `TWELVE_DATA_API_KEY`.
4. شغّل Workflow «🤖 Telegram Commands» مرة للتجربة، ثم سيعمل كل دقيقة تلقائياً.

## ⚠️ ملاحظات
- إن لم ترد أن يعمل كل دقيقة (لتوفير دقائق Actions)، غيّر الـcron مثلاً لكل 2–5 دقائق.
- الأوامر تعمل من **محادثتك الخاصة مع البوت** أو من مجموعة فيها البوت.
- لا تكشف أي بيانات حساسة — كله من قاعدتك عبر الأسرار.

## 🧪 تجربة محلية
```bash
python -m pytest tests/test_telegram_commands.py -q   # 10 passed
```
