# 🏆 Gold AI Signals - نظام إشارات الذهب الذكي

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.10+-green)
![Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals-red)
![Tests](https://img.shields.io/badge/Tests-207%20Passed-blue)

</div>

## 🎯 ما هو Gold AI Signals؟

نظام إرسال إشارات تداول **XAU/USD** يعمل بالكامل على **GitHub Actions** بشكل مجاني، يستخدم **الذكاء الاصطناعي** (Groq/OpenAI/Anthropic) لتحليل السوق ويتعلم من أخطائه ذاتياً.

## ✨ الميزات الرئيسية

| الميزة | الوصف |
|--------|-------|
| 🤖 **12 وكيل ذكي** | تحليل من وجهات نظر متعددة |
| 🧠 **تعلم ذاتي v2.0** | يتعلم من كل صفقة مع Streak Bonus |
| 📊 **AI Providers** | Groq, OpenAI, Anthropic, Gemini |
| 📱 **إشعارات Telegram** | إرسال الإشارات فوراً |
| ⏰ **ساعات التداول** | 11:00 - 17:00 UTC (الأحد-الخميس) |
| 📝 **تقارير يومية** | 23:00 UTC (تقرير + تقييم الوكلاء) |
| 🔄 **مجاناً 100%** | GitHub Actions (2000 دقيقة/شهر) |

## 🔥 شروط الإشارات

```
✅ حد أدنى: 3 وكلاء يوافقون
✅ نسبة توافق: فوق 60%
✅ لا حد أقصى للصفقات
```

## 📁 هيكل المشروع

```
gold-ai-signals/
├── .github/workflows/     # GitHub Actions
│   ├── analyze.yml        # كل 15 دقيقة (11-17 UTC)
│   ├── daily_report.yml   # يومياً 23:00 UTC
│   └── update_trades.yml  # كل ساعة
├── agents/                # الوكلاء الـ 12
├── services/              # الخدمات
│   ├── ai_service.py      # AI محسّن v2.0
│   ├── learning_service.py # تعلم ذكي v2.0
│   └── ...
├── scripts/               # السكريبتات
├── tests/                 # 207 اختبار
├── config.json            # الإعدادات
├── supabase_schema.sql    # قاعدة البيانات
└── requirements.txt       # المكتبات
```

## 🚀 البدء السريع

### 1️⃣ إضافة GitHub Secrets

اذهب إلى: `Settings → Secrets and variables → Actions`

| Secret | الوصف |
|--------|-------|
| `TELEGRAM_BOT_TOKEN` | توكن بوت تلجرام |
| `TELEGRAM_CHAT_ID` | معرف المحادثة |
| `SUPABASE_URL` | رابط Supabase |
| `SUPABASE_KEY` | مفتاح Supabase |
| `GROQ_API_KEY` | مفتاح Groq (أو OPENAI_API_KEY) |

### 2️⃣ تشغيل SQL Schema

1. افتح Supabase Dashboard
2. SQL Editor
3. الصق محتوى `supabase_schema.sql`
4. اضغط "Run"

### 3️⃣ تفعيل Workflows

اذهب إلى: `Actions → Enable all workflows`

## 📊 كيف يعمل النظام؟

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│   🌅 11:00 - 17:00 UTC (أحد-خميس)                          │
│   ────────────────────────────────────────                  │
│   ✅ إرسال الإشارات                                         │
│   ├ تحليل 12 وكيل                                          │
│   ├ التحقق: 3+ وكلاء + 60% توافق                           │
│   ├ AI يحلل (Groq)                                         │
│   └ إرسال Telegram                                         │
│                                                             │
│   🌙 23:00 UTC (يومياً)                                    │
│   ────────────────────────────────────────                  │
│   ✅ تقرير يومي                                             │
│   ✅ تقييم الوكلاء (Learning)                               │
│   ✅ تقرير الصفقات المفتوحة                                │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 🧠 التعلم الذكي v2.0

```
🔥 Streak Bonus:
   ├ 3+ نجاح متتالي → +10% مكافأة
   └ 3+ فشل متتالي → -10% خصم

📊 وزن الصفقات الأخيرة: 60%

🧠 Failed Signals Memory:
   ├ حفظ الصفقات الفاشلة
   └ التعلم منها تلقائياً
```

## ⚠️ إدارة المخاطر

| المستوى | المعادلة |
|---------|----------|
| Stop Loss | 1.5 × ATR |
| Take Profit 1 | 2.0 × ATR |
| Take Profit 2 | 3.5 × ATR |
| Risk per trade | 1-2% |
| Trailing stop | 20 نقطة |

## 📱 نموذج رسالة الإشارة

```
━━━━━━━━━━━━━━━━━━━━
🟢 القرار النهائي
━━━━━━━━━━━━━━━━━━━━
📊 الإشارة: BUY
🎯 الثقة: 85%

🔥 متطلبات التوافق:
├ الوكلاء: 3/3 ✅
├ التوافق: 100% ✅

🗳️ أصوات الوكلاء:
├ شراء: 3 (100%)
├ بيع: 0 (0%)
└ انتظار: 2

🤖 AI: Groq
🧠 التعلم الذكي: ✅ مفعّل
   Win Rate: 65%
━━━━━━━━━━━━━━━━━━━━
```

## 📊 GitHub Actions Minutes

| Workflow | التكرار | الدقائق/شهر |
|----------|---------|------------|
| analyze.yml | كل 15 دقيقة | ~960 |
| update_trades.yml | كل ساعة | ~270 |
| daily_report.yml | يومياً | ~60 |
| **الإجمالي** | | **~1,290** |

✅ **Plan Free (2,000 دقيقة): كافي!**

## 🧪 الاختبارات

```bash
python -m pytest tests/ -v
```

**النتيجة:** 207 اختبار ناجح ✅

## 📈 نسبة النجاح المستهدفة: +80%

## 🤝 التواصل

- **Telegram**: للتحديثات والإشعارات
- **GitHub Issues**: للمشاكل والاقتراحات

---

<div align="center">

**صُنع بـ ❤️ للمتداولين العرب**

**Trade Smart, Trade Safe 🏆**

</div>