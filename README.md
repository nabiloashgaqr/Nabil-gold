# 🏆 Gold AI Signals

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals-blue)
![Groq](https://img.shields.io/badge/AI-GroqCloud-purple)
![Tests](https://img.shields.io/badge/Tests-217%20Passed-brightgreen)
![Mode](https://img.shields.io/badge/Mode-Paper%20Trading-yellow)

</div>

---

## ⚠️ تنبيه

المشروع **تعليمي/تجريبي** لإشارات الذهب **XAU/USD** — لا يُعد توصية مالية.
الوضع الحالي: **Paper Trading** (صفقات افتراضية، بدون تنفيذ حقيقي).

---

## 🎯 المشروع

نظام آلي لتحليل الذهب وإرسال إشارات إلى **Telegram**، يعمل على **GitHub Actions** بوضع **One-Agent + Groq** (قرار Groq إجباري).

**المكوّنات الأساسية:**
- بيانات سوق XAU/USD (Twelve Data)
- 5 وكلاء تحليل فني/سلوكي
- Groq Cloud كبوّابة قرار نهائية
- Supabase لتخزين الصفقات والتعلم
- Telegram للإشعارات
- Backtesting + Dashboard + AI Memory Rules

---

## ✅ الحالة الحالية

| الجزء | الحالة |
|---|---|
| Telegram / Groq / GitHub Actions | ✅ يعمل |
| الاختبارات | ✅ **217/217** ناجح |
| تحذيرات `pyflakes` | ✅ **0** تحذير |
| Paper Trading | ✅ مفعّل |
| Groq كقرار نهائي | ✅ إجباري |
| AI Memory Rules / Backtesting / Dashboard | ✅ مضاف |

---

## 🧠 كيف يعمل النظام

### وضع التشغيل: One-Agent + Groq
- وكيل واحد كافٍ لتوفير السياق
- **Groq فقط** هو من يقرر BUY/SELL/WAIT
- إذا Groq فاشل أو يقول WAIT ← الإشارة تُحجب

### الوكلاء (5 وكلاء)
| الوكيل | الوظيفة |
|---|---|
| `TechnicalAgent` | RSI / MACD / EMA / ATR |
| `ClassicalAgent` | أنماط كلاسيكية + دعم/مقاومة |
| `SMCAgent` | Smart Money (Order Blocks / Liquidity) |
| `PriceActionAgent` | قراءة حركة السعر |
| `MultiTimeframeAgent` | مقارنة الاتجاه عبر الفريمات |

### الفلاتر (يجب أن تجتاز الإشارة كلها)
- ✅ Groq متاح ويقول BUY/SELL بثقة ≥ 60
- ✅ لا توجد أخبار عالية الخطورة (ForexFactory)
- ✅ لا توجد إشارة مكررة في آخر 90 دقيقة
- ✅ داخل وقت التداول (09:00–22:59 Asia/Hebron)
- ✅ Dynamic Risk Manager لا يحظر (بعد 3 خسائر متتالية ← HALT)

### درجة جودة الإشارة (A+ / A / B / C / D)
تظهر في Telegram بناءً على: الثقة + توافق الوكلاء + R:R + إدارة المخاطر + الجلسة.

---

## ⏰ أوقات التشغيل (Asia/Hebron)

| المهمة | التوقيت |
|---|---|
| التحليل وإرسال الإشارات | كل 10 دقائق، 09:00–22:59 (أحد–خميس) |
| تحديث الصفقات المفتوحة | كل ساعة، 09:00–22:59 |
| التقرير اليومي + Learning | 23:00 يوميًا |

---

## 🚀 التشغيل السريع

### 1) أضف GitHub Secrets
من: `Repository → Settings → Secrets and variables → Actions`

| Secret | الوصف |
|---|---|
| `TELEGRAM_BOT_TOKEN` | توكن بوت Telegram |
| `TELEGRAM_CHAT_ID` | رقم القناة |
| `SUPABASE_URL` | رابط مشروع Supabase |
| `SUPABASE_KEY` | Service Role Key |
| `TWELVE_DATA_API_KEY` | مفتاح بيانات السوق |
| `GROQ_API_KEY` | مفتاح Groq Cloud |

### 2) شغّل مخطط قاعدة البيانات
افتح `supabase_schema.sql` في **Supabase SQL Editor** ونفّذه.

### 3) اختبر بالترتيب
1. **📱 Telegram Smoke Test**
2. **🤖 Groq Smoke Test**
3. **✅ Tests**
4. **📊 Gold Analysis Bot** (يدوي)

---

## 🧾 GitHub Actions (9 workflows)

| Workflow | الوظيفة |
|---|---|
| ✅ Tests | تشغيل 217 اختبار |
| 📊 Gold Analysis Bot | التحليل + إرسال الإشارات (كل 10 دقائق) |
| 🔄 Update Open Trades | تحديث SL/TP/Trailing (كل ساعة) |
| 📊 Daily Report & Learning | تقرير نهاية اليوم + تعلّم AI |
| 📊 Dashboard | توليد HTML Dashboard |
| 📱 Telegram Smoke Test | فحص Telegram |
| 🤖 Groq Smoke Test | فحص Groq API |
| 🧪 Backtest | اختبار تاريخي يدوي |
| 🧪 Groq Model Compare | مقارنة نماذج Groq يدويًا |

---

## 🗄️ Supabase Tables

`trades` · `signals` · `agent_weights` · `learning_history` · `agent_evaluations` · `ai_trade_reviews` · `ai_memory_rules` · `portfolio` · `daily_reports` · `news_log` · `session_log` · `risk_settings`

---

## 📁 هيكل المشروع

```
Nabil-gold/
├── .github/workflows/      9 workflows (analyze, tests, daily_report, ...)
├── agents/                 13 ملف (decision, technical, classical, smc, ...)
├── services/               16 ملف (ai_service, database, telegram_bot, ...)
├── scripts/                11 ملف (run_analysis, run_trade_updates, ...)
├── tests/                  18 ملف — 217 اختبار
├── utils/                  helpers + indicators
├── config.json             الإعدادات الرئيسية
├── supabase_schema.sql     مخطط قاعدة البيانات
├── requirements.txt        requests, pandas, supabase, httpx, pytest
└── main.py                 نقطة دخول محلية
```

---

## 🧪 الاختبارات

```bash
# تشغيل محلي
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

**النتيجة:** `217 passed` في ~1 ثانية · تغطية ~63%

---

## 🛠️ التطويرات الأخيرة

### Reliability (P0)
- ✅ Groq retry/backoff (3 محاولات، exponential 1s/2s)
- ✅ Synthetic data guard (يحجب الإنتاج على بيانات وهمية)
- ✅ Supabase strict mode (لا fallback إلى JSON محلي)
- ✅ Workflow concurrency (منع تداخل التشغيلات)
- ✅ NEUTRAL/HOLD/NO_TRADE → WAIT (توحيد)

### Quality (P1)
- ✅ Groq Observation Mode: One-Agent + Groq إجباري
- ✅ ForexFactory news feed (مجاني، بدون API key)
- ✅ sanitize_rule_text() (منع prompt injection في AI Memory Rules)
- ✅ Dynamic Risk Manager (HALT بعد 3 خسائر، CAUTION بعد 2)
- ✅ Duplicate Signal Filter (نافذة 90 دقيقة)
- ✅ AI Memory Rules من Trade Review (قواعد تحسّن القرارات القادمة)
- ✅ Trailing Stop بعد TP1 + Partial Close 50%

### Code Quality
- ✅ **217/217** اختبار ناجح
- ✅ **0** تحذير `pyflakes` (كان 27)
- ✅ NameError الحرج في `_final_decision` مُصلَح
- ✅ الكود الميت في `decision_agent.py` محذوف

---

## 🧭 خارطة الطريق القادمة

1. **Weekly AI Performance Report** — تقرير أسبوعي يكتبه Groq
2. **Telegram Commands** — `/status`, `/open`, `/report`, `/pause`
3. **GitHub Pages Dashboard** — رابط دائم بدل Artifact
4. **Backtest مع Groq اختياريًا** — تحليل جودة قرارات Groq تاريخيًا

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**

</div>
