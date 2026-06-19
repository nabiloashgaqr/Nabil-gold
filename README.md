# 🏆 Gold AI Signals — نظام إشارات الذهب الذكي

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals-blue)
![Groq](https://img.shields.io/badge/AI-GroqCloud-purple)
![Tests](https://img.shields.io/badge/Tests-207%20Passed-brightgreen)
![Mode](https://img.shields.io/badge/Mode-Paper%20Trading-yellow)

</div>

---

## ⚠️ تنبيه مهم

هذا المشروع تعليمي/تجريبي لإشارات الذهب **XAU/USD**، ولا يمثل توصية مالية أو تنفيذًا آليًا للصفقات. النظام يعمل حاليًا بوضع:

```text
Paper Trading
```

أي أنه يحفظ ويتابع الصفقات افتراضيًا لأغراض الاختبار والتحسين.

---

## 🎯 ما هو المشروع؟

**Gold AI Signals** هو نظام آلي لتحليل الذهب وإرسال إشارات إلى Telegram، يعمل على GitHub Actions، ويستخدم:

- بيانات سوق XAU/USD
- عدة وكلاء تحليل فني وسلوكي
- Groq Cloud كذكاء اصطناعي إجباري لاتخاذ القرار النهائي
- Supabase كذاكرة دائمة للصفقات والتعلم
- Telegram لإرسال الإشارات والتقارير والتنبيهات
- Backtesting وDashboard وذاكرة أخطاء لتحسين الأداء تدريجيًا

---

## ✅ الحالة الحالية للنظام

| الجزء | الحالة |
|---|---|
| Telegram | ✅ يعمل |
| Groq Smoke Test | ✅ يعمل |
| GitHub Actions | ✅ يعمل |
| Tests | ✅ 207 اختبار ناجح |
| Paper Trading | ✅ مفعّل |
| Groq إجباري | ✅ مفعّل |
| منع الإشارات الضعيفة | ✅ مفعّل |
| Dashboard | ✅ مضاف |
| AI Memory Rules | ✅ مضافة |
| Backtesting | ✅ مضاف |

---

## 🧠 الميزات الرئيسية

### 1. 🤖 Groq Cloud إجباري لاتخاذ القرار

النظام مضبوط لاستخدام Groq Cloud عبر:

```text
GROQ_API_KEY
```

إذا فشل Groq أو لم يكن المفتاح موجودًا، لا يرجع النظام لإشارة كلاسيكية عشوائية، بل يمنع الإشارة.

**الفائدة:** القرار النهائي لا يخرج إلا بعد تحليل AI.

---

### 2. 🧩 نظام وكلاء متعدد Agents

يستخدم المشروع عدة وكلاء تحليل، منها:

| الوكيل | الوظيفة |
|---|---|
| TechnicalAgent | مؤشرات فنية مثل RSI / MACD / EMA / ATR |
| ClassicalAgent | أنماط كلاسيكية ودعم/مقاومة |
| SMCAgent | Smart Money Concepts مثل Order Blocks وLiquidity |
| PriceActionAgent | قراءة حركة السعر والشموع |
| MultiTimeframeAgent | مقارنة الاتجاه عبر أكثر من فريم |
| NewsRiskAgent | فحص مخاطر الأخبار |
| RiskManagementAgent | حساب SL/TP/R:R وحجم الصفقة |
| DecisionAgent | دمج كل النتائج واتخاذ القرار النهائي |
| OpenTradesManager | متابعة الصفقات المفتوحة |
| DailyReportAgent | توليد التقرير اليومي |

---

### 3. 📊 شروط إرسال الإشارة

لا يتم إرسال BUY/SELL إلا إذا تحققت الشروط الأساسية:

```text
- Groq يعمل
- القرار BUY أو SELL
- الثقة فوق الحد الأدنى
- إدارة المخاطر وافقت
- لا توجد أخبار خطيرة تمنع التداول
- ليست إشارة مكررة
- داخل وقت التداول التجريبي
```

الحد الأدنى الحالي للثقة:

```json
"min_confidence": 60
```

---

### 4. ⭐ Signal Quality Score

كل إشارة تحصل على تقييم جودة:

```text
A+ / A / B / C / D
```

يعتمد التقييم على:

- ثقة الإشارة
- توافق الوكلاء
- Risk/Reward
- موافقة إدارة المخاطر
- الأخبار
- جودة الجلسة
- التحذيرات

ويظهر في Telegram مثل:

```text
⭐ جودة الإشارة: B / 72.5% (Good)
```

---

### 5. 🔁 Duplicate Signal Filter

يمنع النظام تكرار نفس الإشارة إذا:

- توجد صفقة مفتوحة بنفس الاتجاه
- أو تم إرسال إشارة مشابهة حديثًا
- أو السعر قريب جدًا من إشارة سابقة

الإعدادات:

```json
"duplicate_signal_filter": {
  "enabled": true,
  "lookback_minutes": 90,
  "price_tolerance_points": 3.0,
  "price_tolerance_atr_multiplier": 0.75,
  "block_if_open_same_direction": true
}
```

**الفائدة:** تقليل التكرار والصفقات المتشابهة.

---

### 6. 🧪 Paper Trading Mode

النظام يعمل بوضع تجريبي:

```json
"trading_mode": "paper"
```

كل إشارة يتم حفظها كصفقة افتراضية، ويتم تتبعها دون تنفيذ حقيقي.

يتم حفظ:

- نوع الصفقة
- الدخول
- وقف الخسارة
- الأهداف
- الثقة
- حالة الصفقة
- الربح/الخسارة الافتراضية
- وضع Paper Trading

---

### 7. 🧠 AI Decision Explanation

رسالة Telegram لا تعرض الأرقام فقط، بل تعرض شرح Groq:

```text
🤖 تحليل Groq:
├ الاتجاه
├ سبب الدخول
├ خطر الاتجاه المعاكس
├ ملاحظات المخاطر
└ الخطة
```

**الفائدة:** تفهم لماذا تم إرسال الإشارة، وليس فقط أين تدخل وتخرج.

---

### 8. 📱 Telegram Notifications

النظام يرسل إلى Telegram:

- إشارات BUY/SELL
- تحديثات الصفقات المفتوحة
- نتائج TP/SL
- التقرير اليومي
- مراجعات Groq للخسائر
- نتائج اختبار Groq
- نتائج Backtest
- تحديث Dashboard

يوجد أيضًا Workflow لاختبار Telegram:

```text
📱 Telegram Smoke Test
```

---

### 9. 🧪 Groq Smoke Test

تمت إضافة Workflow مستقل لاختبار Groq:

```text
🤖 Groq Smoke Test
```

يتأكد من:

- وجود `GROQ_API_KEY`
- صحة الاتصال بـ Groq
- صحة الموديل
- قدرة Groq على إرجاع JSON

---

### 10. 🧪 Backtesting

تمت إضافة Backtesting خفيف:

```text
🧪 Backtest
```

الملفات:

```text
services/backtesting.py
scripts/run_backtest.py
.github/workflows/backtest.yml
```

يقيس:

- عدد الصفقات
- Win Rate
- Net Points
- Profit Factor
- Max Drawdown
- أداء BUY مقابل SELL
- متوسط جودة الإشارات

> ملاحظة: Backtesting يعمل Classic/offline ولا يستخدم Groq افتراضيًا حتى لا يستهلك API بكثرة.

---

### 11. 🧠 AI Trade Review للخسائر

عند إغلاق صفقة بخسارة، يقوم Groq بمراجعتها واستخراج سبب الخسارة.

يحفظ في جدول:

```text
ai_trade_reviews
```

يحاول تحديد:

- سبب الخسارة
- هل الدخول مبكر؟
- هل الاتجاه خطأ؟
- هل وقف الخسارة ضيق؟
- هل الأخبار أثرت؟
- ما القواعد المقترحة للتحسين؟

---

### 12. 🧠 AI Memory Rules Engine

هذه من أهم التطويرات.

النظام يحوّل مراجعات Groq للخسائر إلى قواعد ذاكرة دائمة في Supabase:

```text
ai_memory_rules
```

مثال قاعدة:

```text
لا تدخل SELL قرب دعم قوي دون تأكيد إغلاق شمعة 15m
```

ثم يقرأ النظام هذه القواعد في التحليل القادم ويضيفها إلى Prompt قرار Groq.

**الفائدة:** النظام لا يراجع الخسائر فقط، بل يتذكر الدروس ويستخدمها لاحقًا.

---

### 13. 📊 Dashboard HTML

تمت إضافة لوحة تحكم HTML يتم إنشاؤها من GitHub Actions:

```text
📊 Dashboard
```

تعرض:

- إجمالي الصفقات
- الصفقات المفتوحة
- Win Rate
- Net Points
- Profit Factor
- متوسط الثقة
- آخر الصفقات
- AI Trade Reviews
- Active Memory Rules

ويتم رفعها كـ Artifact باسم:

```text
gold-dashboard
```

---

### 14. 📰 News Risk Filter

يوجد وكيل أخبار يمنع أو يقلل الثقة حول الأخبار المؤثرة.

الإعدادات تشمل:

```json
"news_feed": {
  "enabled": true,
  "hours_before": 2,
  "hours_after": 1,
  "min_impact": "medium",
  "auto_block_on_high": true
}
```

---

### 15. 📈 Trailing Stop / إدارة الصفقة

يدعم المشروع متابعة الصفقات المفتوحة:

- TP1
- TP2
- SL
- Break-even
- Trailing stop
- إغلاق جزئي افتراضيًا
- تنبيهات Telegram عند الأحداث المهمة

---

### 16. 🧭 Daily Bias Filter

فلتر اتجاه أعلى يستخدم فريم 4H كمرجع عملي للاتجاه العام، ويمنع الصفقات الضعيفة عكس الاتجاه.

مثال:

```text
Daily Bias = Bullish
BUY مسموح طبيعيًا
SELL يحتاج ثقة أعلى أو يتحول إلى WAIT
```

الإعدادات:

```json
"daily_bias_filter": {
  "enabled": true,
  "timeframe": "4H",
  "contrarian_min_confidence": 80
}
```

---

### 17. 📰 AI News Interpretation

Groq لا يكتفي بقراءة وجود الأخبار، بل يفسر تأثيرها المحتمل على الذهب والدولار:

- هل الخبر يدعم الذهب أم الدولار؟
- هل يجب منع التداول؟
- هل يُسمح باتجاه واحد فقط؟
- كم يجب الانتظار؟

ويظهر أثره في القرار النهائي ورسالة Telegram.

---

### 18. 🛡️ Dynamic Risk Management

إدارة مخاطرة ديناميكية ترفع شروط الإشارة أو توقف التداول مؤقتًا عند تدهور الأداء.

تعمل حسب:

- عدد الخسائر المتتالية
- خسارة اليوم بالنقاط
- أداء آخر الصفقات
- جودة الإشارة
- ثقة القرار

مثال:

```text
بعد خسارتين متتاليتين → وضع STRICT
مطلوب ثقة أعلى وجودة أعلى
بعد 3 خسائر → HALT مؤقت
```

الإعدادات الأساسية:

```json
"dynamic_risk_management": {
  "enabled": true,
  "warn_after_losses": 2,
  "halt_after_losses": 3,
  "daily_loss_limit_points": 30,
  "strict_min_confidence": 82,
  "strict_min_quality_score": 80
}
```

---

## ⏰ أوقات التشغيل الحالية

تم ضبط النظام للتجربة حسب توقيت:

```text
Asia/Jerusalem
```

### التحليل

```text
كل 15 دقيقة تقريبًا
من 07:59 صباحًا إلى 18:01 مساءً بتوقيت Asia/Jerusalem
الأحد إلى الخميس
```

### تحديث الصفقات المفتوحة

```text
كل ساعة
من 07:59 صباحًا إلى 18:01 مساءً بتوقيت Asia/Jerusalem
الأحد إلى الخميس
```

### جلسة نهاية اليوم

```text
23:00 بتوقيت Asia/Jerusalem
```

وتشمل:

- تحديث آخر اليوم للصفقات المفتوحة
- Learning Update
- AI Trade Review للخسائر
- التقرير اليومي
- تقرير الصفقات المفتوحة

---

## 🧾 GitHub Actions الحالية

| Workflow | الوظيفة |
|---|---|
| ✅ Tests | تشغيل الاختبارات |
| 📊 Gold Analysis Bot | التحليل وإرسال الإشارات |
| 🔄 Update Open Trades | تحديث الصفقات المفتوحة |
| 📊 Daily Report & Learning | التقرير اليومي والتعلم |
| 📱 Telegram Smoke Test | اختبار Telegram |
| 🤖 Groq Smoke Test | اختبار Groq |
| 🧪 Backtest | اختبار تاريخي |
| 📊 Dashboard | توليد لوحة التحكم |

---

## 🔐 GitHub Secrets المطلوبة

أضفها من:

```text
Repository → Settings → Secrets and variables → Actions
```

| Secret | الوصف |
|---|---|
| `TELEGRAM_BOT_TOKEN` | توكن بوت Telegram |
| `TELEGRAM_CHAT_ID` | رقم القناة/المحادثة |
| `SUPABASE_URL` | رابط مشروع Supabase |
| `SUPABASE_KEY` | يفضل Service Role Key |
| `TWELVE_DATA_API_KEY` | مفتاح بيانات السوق |
| `GROQ_API_KEY` | مفتاح Groq Cloud |

---

## 🗄️ Supabase Tables

الجداول الأساسية:

```text
trades
signals
agent_weights
learning_history
agent_evaluations
ai_trade_reviews
ai_memory_rules
portfolio
daily_reports
news_log
session_log
risk_settings
```

ملف SQL:

```text
supabase_schema.sql
```

يمكن تشغيله من Supabase SQL Editor.

---

## 🚀 طريقة التشغيل السريعة

1. أضف GitHub Secrets.
2. شغل `supabase_schema.sql` في Supabase.
3. اختبر Telegram:
   ```text
   📱 Telegram Smoke Test
   ```
4. اختبر Groq:
   ```text
   🤖 Groq Smoke Test
   ```
5. شغل Tests:
   ```text
   ✅ Tests
   ```
6. شغل التحليل يدويًا:
   ```text
   📊 Gold Analysis Bot
   ```
7. راقب Telegram وSupabase.

---

## 📁 هيكل المشروع المختصر

```text
Nabil-gold/
├── .github/workflows/
│   ├── analyze.yml
│   ├── update_trades.yml
│   ├── daily_report.yml
│   ├── telegram_test.yml
│   ├── groq_test.yml
│   ├── backtest.yml
│   ├── dashboard.yml
│   └── tests.yml
├── agents/
│   ├── decision_agent.py
│   ├── risk_management_agent.py
│   ├── technical_agent.py
│   ├── smc_agent.py
│   └── ...
├── services/
│   ├── ai_service.py
│   ├── database.py
│   ├── market_data.py
│   ├── telegram_bot.py
│   ├── backtesting.py
│   ├── trade_review.py
│   ├── memory_rules.py
│   ├── dashboard.py
│   └── ...
├── scripts/
│   ├── run_analysis.py
│   ├── run_trade_updates.py
│   ├── run_daily_report.py
│   ├── run_backtest.py
│   ├── run_trade_review.py
│   ├── generate_dashboard.py
│   └── ...
├── tests/
├── config.json
├── supabase_schema.sql
└── requirements.txt
```

---

## 🧪 الاختبارات

تشغيل محلي:

```bash
python -m pytest -q
```

الحالة الحالية:

```text
207 passed
```

---

## ✅ التطويرات التي تمت مؤخرًا

### المرحلة الأولى — إصلاح التشغيل

- إصلاح ربط `DecisionAgent`
- توحيد صيغة القرار النهائي
- إصلاح `run_daily_report.py`
- إصلاح `run_learning.py`
- توحيد Supabase schema مع الكود
- منع الإشارات على بيانات وهمية في الإنتاج
- إصلاح مشاكل async tests

### المرحلة الثانية — تجهيز الإنتاج

- إضافة `validate_setup.py`
- إضافة Workflow للاختبارات
- إضافة Telegram Smoke Test
- إضافة Groq Smoke Test
- جعل Groq إجباريًا
- إصلاح فلتر الثقة
- ضبط أوقات التشغيل حسب Asia/Jerusalem

### مرحلة الذكاء والتحسين

- Duplicate Signal Filter
- Signal Quality Score
- AI Decision Explanation
- Paper Trading Mode
- Backtesting
- AI Trade Review للخسائر
- AI Memory Rules Engine
- HTML Dashboard
- Daily Bias Filter
- AI News Interpretation
- Dynamic Risk Management

---

## 🧭 ما تبقى من المقترحات القادمة

## 2. Weekly AI Performance Report

تقرير أسبوعي يكتبه Groq يوضح:

- أفضل يوم تداول
- أسوأ يوم
- أفضل وكيل
- أسباب الخسائر
- توصيات الأسبوع القادم

---

### 2. Telegram Commands

أوامر مثل:

```text
/status
/open
/report
/performance
/pause
/resume
/last
```

> ملاحظة: هذه تحتاج آلية تشغيل مستمرة أو Webhook، لذلك هي أصعب من باقي التحسينات على GitHub Actions فقط.

---

### 3. GitHub Pages Dashboard

تحويل Dashboard من Artifact إلى رابط دائم مثل:

```text
https://nabiloashgaqr.github.io/Nabil-gold/dashboard.html
```

---

### 4. Backtest متقدم بـ Groq اختياريًا

تشغيل Groq على عدد محدود من نقاط الاختبار فقط لتحليل جودة قراراته تاريخيًا بدون استهلاك كبير.

---

## 📌 أفضل خطوة تالية مقترحة

بعد إضافة Dynamic Risk Management، أفضل خطوة تالية هي:

```text
Weekly AI Performance Report
```

لأنه يلخص أداء الأسبوع ويعطي توصيات استراتيجية لتحسين النظام.

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**

</div>
