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


---


---


---


---

## 🧪 وضع تجربة الإشارات من وكيل واحد

تم تفعيل وضع تجريبي مؤقت داخل `DecisionAgent` يسمح بخروج إشارة من **أقوى وكيل واحد** حتى لو لم تتحقق نسبة توافق الوكلاء المعتادة.

الإعداد في `config.json`:

```json
"experimental_single_agent": {
  "enabled": true,
  "min_confidence": 1,
  "bypass_groq_min_confidence": true
}
```

### ماذا يظهر في Telegram؟

ستظهر الإشارة مع سطر يوضح مصدرها:

```text
🧪 مصدر الإشارة التجريبي: smc | BUY | موثوقية D (55.0%)
```

### ما الذي لا يزال محميًا؟

رغم أن النسبة والتوافق مخففان للتجربة، ما زالت الفلاتر التالية تعمل:

- وقت التداول
- الأخبار الخطيرة
- RiskManagement veto
- DynamicRisk HALT/DAILY_HALT
- Duplicate Signal Filter
- الحد الأقصى للصفقات المفتوحة

### الهدف

هذا الوضع مخصص فقط لمراقبة خروج الإشارات تلقائيًا وفهم أداء كل وكيل، ثم لاحقًا نعيد التشديد إلى 3 وكلاء وتوافق أعلى.

---

## 🛠️ تحديثات كود فعلية للوكلاء بعد ملف Arena

بالإضافة إلى إدخال Agent Playbooks في Prompt قرار Groq، تم تنفيذ تطويرات فعلية داخل الكود:

### NewsRiskAgent v3.1

تمت إضافة تطويرات فعلية لمخاطر الأخبار:

- Tier 1 / Tier 2 / Tier 3 event classification
- نوافذ منع مختلفة حسب نوع الخبر
- FOMC / Interest Rate / CPI / PCE / NFP special handling
- High Risk Day إذا وُجدت 3 أحداث Tier 1 خلال 24 ساعة
- tier_summary في المخرجات
- special_handling لكل حدث
- risk_score أدق حسب توقيت الخبر وقوته

### RiskManagementAgent v3.1

تمت إضافة تطويرات فعلية لإدارة المخاطر:

- Trade Grade: A+ / A / B / C / D / F
- Grade-based risk multiplier
- رفض Grade D/F تلقائيًا
- تقليل المخاطرة لصفقات C وB
- فلتر max daily signals داخل RiskAgent
- حساب risk percent وrisk amount وlot size بناءً على multiplier
- تقييم R:R وSL/ATR وتوافق الوكلاء وMTF وDaily Bias داخل درجة المخاطرة
- عرض Risk Grade في Telegram

### MultiTimeframeAgent v3.1

تمت إضافة تطويرات فعلية لتحليل الفريمات:

- Timeframe hierarchy واضح: 4H → 1H → 15m → 5m
- Alignment Score موزون حسب أهمية الفريم
- Conflict Matrix بين الفريمات
- Counter-trend penalty ضد الفريم الأعلى
- Setup Type: TREND_CONTINUATION / PULLBACK_ENTRY / REVERSAL_ATTEMPT / INTRADAY_ALIGNMENT
- Recommended Entry Timeframe
- Pullback detection إلى EMA20/EMA50
- تخفيض الثقة عند تعارض الفريمات أو محاولة انعكاس ضد الاتجاه الأعلى

### ClassicalAgent v3.1

تمت إضافة تطويرات فعلية للتحليل الكلاسيكي:

- Double Top / Double Bottom
- Triple Top / Triple Bottom
- Ascending Triangle / Descending Triangle
- Symmetrical Triangle
- Rising Wedge / Falling Wedge
- Ascending / Descending Channel
- Pattern completion %
- Pattern status: FORMING / COMPLETE / NONE
- Level strength حسب عدد اللمسات
- NO_CLEAR_PATTERN protection لمنع إجبار نمط غير واضح
- أهداف كلاسيكية مبنية على عرض النمط أو neckline

### SMCAgent v3.1

تمت إضافة تحسينات مؤسسية فعلية داخل الكود:

- Order Block mitigation status: FRESH / TESTED / MITIGATED / INVALIDATED
- Displacement quality: STRONG / MODERATE / WEAK
- Order Block equilibrium
- FVG size وstrength وpartial fill
- Liquidity Sweep confirmation: STRONG / MODERATE
- Scoring أدق للـ OB/FVG/Sweep حسب القوة والحالة
- تجاهل المناطق invalidated في scoring

### TechnicalAgent v3.1

أصبح يحسب المؤشرات من الشموع مباشرة إذا لم تكن جاهزة، ويضيف:

- EMA Ribbon: 8 / 21 / 50 / 100 / 200
- RSI-14 وRSI-7
- RSI Divergence مبسط
- MACD Histogram Slope
- ATR Percentile وVolatility Regime
- Bollinger Bands و%B وSqueeze
- ADX proxy لقياس قوة الترند
- Support/Resistance من الشموع
- منع حالة WAIT من الظهور بثقة عالية

### PriceActionAgent v3.1

تمت إضافة نماذج شموع جديدة:

- Bullish/Bearish Harami
- Piercing Pattern
- Dark Cloud Cover
- Tweezer Top / Bottom
- Bullish/Bearish Marubozu
- Spinning Top
- Dragonfly Doji
- Gravestone Doji
- Inverted Hammer
- Hanging Man / Upper Rejection

مع مراعاة:

- موقع النموذج عند دعم/مقاومة
- الترند السابق قبل النموذج
- قوة الجسم والذيل
- ATR tolerance للمستويات المتقاربة

---

## 📘 Agent Playbooks v3.0 من ملف Arena

تمت قراءة الملف المرفوع:

```text
Arena _ Benchmark & Compare the Best AI Models.html
```

واستخلاص قواعد تشغيل متقدمة للوكلاء، ثم تطبيقها داخل المشروع في ملف:

```text
services/agent_playbooks.py
```

هذه القواعد أصبحت تدخل مباشرة في Prompt قرار Groq النهائي عبر `DecisionAgent`، بحيث لا يكتفي Groq بأصوات الوكلاء فقط، بل يراجع أيضًا معايير كل وكيل حسب تخصصه قبل إصدار BUY/SELL/WAIT.

### ماذا تحتوي Playbooks؟

| الوكيل | أهم ما أضيف من ملف Arena |
|---|---|
| TechnicalAgent | RSI/MACD/EMA/ATR/Bollinger/ADX وفحص التعارض بين المؤشرات |
| ClassicalAgent | H&S، Double/Triple Tops، Triangles، Flags، Wedges، S/R بشروط لمس تاريخية |
| SMCAgent | Order Blocks، Liquidity Sweeps، FVG، BOS/CHoCH، Premium/Discount |
| PriceActionAgent | شموع مفصلة مثل Hammer, Engulfing, Harami, Piercing, Dark Cloud, Doji variants |
| MultiTimeframeAgent | قاعدة الاتجاه الأعلى أولًا والدخول من الفريم الأدنى |
| NewsRiskAgent | Tier 1/2 events، FOMC، CPI، NFP، قرارات البنوك المركزية |
| RiskManagementAgent | رأس المال أولًا، veto power، position sizing، drawdown/loss limits |
| DecisionAgent | لا يتجاوز Veto، لا Grade D/F، جودة قبل الكمية، انتظار عند التعارض |
| OpenTradesManager | متابعة TP/SL/BE/Trailing/long-running/expiry |
| DailyReportAgent | تقرير أداء شامل وتوصيات تحسين مستمرة |

### كيف تم التطبيق؟

في كل تحليل، يرسل `DecisionAgent` إلى Groq:

```text
- أصوات الوكلاء
- Daily Bias
- AI News Interpretation
- Dynamic Risk
- Memory Rules
- Agent Playbooks v3.0
```

وهذا يجعل قرار Groq النهائي ملتزمًا بقواعد كل وكيل ووظيفته.

---

## 🤖 شروط التداول لكل وكيل Trading Conditions by Agent

هذا القسم يوضح وظيفة كل وكيل داخل النظام، ومتى يعطي موافقة أو رفض أو انتظار. الهدف أن تكون شروط التداول واضحة ومكتوبة داخل README، بحيث يمكن مراجعة منطق كل وكيل قبل الاعتماد على الإشارات.

> ملاحظة: القرار النهائي لا يعتمد على وكيل واحد فقط، بل على دمج أصوات الوكلاء + Groq + إدارة المخاطر + الأخبار + الذاكرة + Dynamic Risk.

---

### 1. TechnicalAgent — وكيل التحليل الفني

**الوظيفة:** قراءة المؤشرات الفنية الأساسية للذهب مثل EMA وRSI وMACD وATR ومستويات الدعم والمقاومة.

**يعطي BUY عندما:**

- السعر يميل أعلى المتوسطات المهمة.
- الزخم إيجابي عبر MACD أو ميل المتوسطات.
- RSI ليس في تشبع شراء مبالغ فيه.
- يوجد دعم قريب أو بنية فنية تدعم الصعود.
- ATR كافٍ لإعطاء مساحة حركة للصفقة.

**يعطي SELL عندما:**

- السعر يميل أسفل المتوسطات المهمة.
- الزخم سلبي عبر MACD أو ميل المتوسطات.
- RSI ليس في تشبع بيع مبالغ فيه.
- توجد مقاومة قريبة أو بنية فنية تدعم الهبوط.
- ATR كافٍ لتحرك الصفقة.

**يعطي WAIT عندما:**

- المؤشرات متضاربة.
- RSI في منطقة تشبع خطيرة.
- ATR ضعيف.
- السعر في منتصف نطاق بدون دعم/مقاومة واضحة.

---

### 2. ClassicalAgent — وكيل التحليل الكلاسيكي

**الوظيفة:** تحليل النماذج الكلاسيكية، الترندات، الدعوم، المقاومات، والأنماط السعرية البسيطة.

**يعطي BUY عندما:**

- السعر فوق دعم مهم.
- يوجد كسر أو ارتداد من مستوى كلاسيكي.
- الميل العام أو خط الاتجاه يدعم الصعود.
- النمط الكلاسيكي يميل للصعود.

**يعطي SELL عندما:**

- السعر تحت مقاومة مهمة.
- يوجد كسر دعم أو رفض من مقاومة.
- خط الاتجاه أو البنية الكلاسيكية تميل للهبوط.
- النمط الكلاسيكي يميل للبيع.

**يعطي WAIT عندما:**

- السعر بين دعم ومقاومة بدون أفضلية.
- النمط غير مكتمل.
- لا توجد مستويات واضحة.

---

### 3. SMCAgent — وكيل Smart Money Concepts

**الوظيفة:** قراءة بنية السوق الذكية: Order Blocks، Liquidity Sweeps، Fair Value Gaps، Premium/Discount، Market Structure.

**يعطي BUY عندما:**

- البنية تميل لصعود أو حدث CHoCH/BOS صاعد.
- السعر في Discount أو قرب Order Block صاعد.
- حدث sweep للسيولة السفلية ثم رجوع السعر.
- توجد FVG أو منطقة طلب تدعم الصعود.

**يعطي SELL عندما:**

- البنية تميل لهبوط أو حدث CHoCH/BOS هابط.
- السعر في Premium أو قرب Order Block هابط.
- حدث sweep للسيولة العلوية ثم رجوع السعر.
- توجد FVG أو منطقة عرض تدعم الهبوط.

**يعطي WAIT عندما:**

- البنية غير واضحة.
- السعر في منتصف النطاق.
- لا توجد سيولة أو Order Block واضح.

---

### 4. PriceActionAgent — وكيل حركة السعر والشموع

**الوظيفة:** تحليل الشموع وحركة السعر مثل Engulfing، Pin Bar، Doji، Inside Bar، Morning/Evening Star، Three Soldiers/Crows، Breakouts، False Breakouts، Rejections.

**يعطي BUY عندما:**

- ظهور شمعة صعودية قوية بإغلاق قرب القمة.
- Bullish Engulfing أو Hammer/Pin Bar عند دعم.
- Morning Star أو Three White Soldiers.
- اختراق مقاومة بجسم قوي أو false breakdown صاعد.
- رفض سعري صاعد من دعم.

**يعطي SELL عندما:**

- ظهور شمعة هبوطية قوية بإغلاق قرب القاع.
- Bearish Engulfing أو Shooting Star عند مقاومة.
- Evening Star أو Three Black Crows.
- كسر دعم بجسم قوي أو false breakout هابط.
- رفض سعري هابط من مقاومة.

**يعطي WAIT/REJECT عندما:**

- شمعة Doji أو تردد في منتصف النطاق.
- اختراق ضعيف بدون إغلاق واضح.
- نموذج شموع بعيد عن دعم/مقاومة.
- آخر 3 شموع تعطي سياقًا مختلطًا.

**تطوير مقترح لهذا الوكيل:** إضافة Harami، Piercing، Dark Cloud، Tweezer، Marubozu، Dragonfly/Gravestone Doji، Hanging Man، Inverted Hammer، وفهم أعمق للترند السابق قبل النموذج.

---

### 5. MultiTimeframeAgent — وكيل تعدد الأطر الزمنية

**الوظيفة:** مقارنة الاتجاه بين أكثر من فريم مثل 5m و15m و1H و4H.

**يعطي BUY عندما:**

- أغلب الفريمات متوافقة صعودًا.
- فريم الاتجاه الأكبر يدعم الشراء.
- فريم الدخول لا يعاكس الاتجاه العام.
- بنية القمم والقيعان تميل للصعود.

**يعطي SELL عندما:**

- أغلب الفريمات متوافقة هبوطًا.
- فريم الاتجاه الأكبر يدعم البيع.
- فريم الدخول لا يعاكس الاتجاه العام.
- بنية القمم والقيعان تميل للهبوط.

**يعطي WAIT عندما:**

- الفريمات متضاربة.
- 15m يعاكس 4H بقوة.
- لا توجد محاذاة كافية.

---

### 6. DailyBiasAgent — وكيل الاتجاه الأعلى

**الوظيفة:** تحديد الميل الأعلى باستخدام فريم 4H كمرجع عملي للاتجاه الأكبر.

**يعطي BULLISH عندما:**

- السعر أعلى EMA البطيء.
- EMA السريع أعلى EMA البطيء.
- ميل السعر على الفريم الأعلى إيجابي.
- RSI يميل للصعود.

**يعطي BEARISH عندما:**

- السعر أسفل EMA البطيء.
- EMA السريع أسفل EMA البطيء.
- ميل السعر على الفريم الأعلى سلبي.
- RSI يميل للهبوط.

**التأثير على التداول:**

- إذا كان الاتجاه BULLISH، صفقات SELL تحتاج ثقة أعلى.
- إذا كان الاتجاه BEARISH، صفقات BUY تحتاج ثقة أعلى.
- إذا كان الاتجاه NEUTRAL، لا يتم منع الإشارة بسبب الاتجاه.

الإعداد الحالي:

```json
"contrarian_min_confidence": 80
```

---

### 7. NewsRiskAgent — وكيل مخاطر الأخبار

**الوظيفة:** فحص الأخبار المؤثرة على الذهب والعملات المرتبطة به مثل USD وEUR وGBP وJPY وغيرها.

**يسمح بالتداول عندما:**

- لا توجد أخبار عالية التأثير قريبة.
- الأخبار منخفضة أو متوسطة ولا تفرض منعًا مباشرًا.
- فترة السوق ليست عالية الخطورة.

**يمنع أو يحذر عندما:**

- توجد أخبار HIGH قريبة.
- الخبر مرتبط بالدولار أو بيانات مؤثرة على الذهب.
- المخاطر الزمنية قبل/بعد الخبر داخل نافذة المنع.

**المخرجات المهمة:**

```text
SAFE / CAUTION / DANGER / HIGH_VOLATILITY
```

---

### 8. NewsInterpreter — تفسير الأخبار بالذكاء الاصطناعي

**الوظيفة:** استخدام Groq لتفسير الخبر اقتصاديًا، وليس فقط معرفة وجوده.

**قد يسمح فقط بـ BUY أو SELL عندما:**

- الخبر يدعم الدولار أو يضعفه.
- التأثير المتوقع على الذهب واضح.
- اتجاه واحد أكثر أمانًا من الآخر.

**يمنع التداول عندما:**

- Groq يعتبر المخاطر HIGH/EXTREME.
- `block_trading = true`.
- `allowed_direction = NONE`.

**مثال:**

```text
CPI أعلى من المتوقع → الدولار قوي → الذهب سلبي → السماح SELL فقط أو منع التداول مؤقتًا.
```

---

### 9. RiskManagementAgent — وكيل إدارة المخاطر

**الوظيفة:** حساب الدخول، وقف الخسارة، الأهداف، R:R، حجم الصفقة، وتطبيق فلاتر المخاطر.

**يعتمد الصفقة عندما:**

- الاتجاه واضح من الوكلاء.
- ATR مناسب.
- السبريد ضمن الحد.
- R:R يحقق الحد الأدنى.
- وقف الخسارة ليس واسعًا جدًا.
- الهدف الأول ليس قريبًا جدًا.
- عدد الصفقات المفتوحة أقل من الحد.
- لا توجد خسائر متتالية تتجاوز الحد.

**يرفض الصفقة عندما:**

- ATR منخفض.
- السبريد عالي.
- R:R منخفض.
- SL واسع جدًا.
- الهدف قريب جدًا.
- وصلنا للحد الأقصى للصفقات.
- توجد خسائر متتالية تتطلب التهدئة.

---

### 10. DynamicRiskManager — إدارة المخاطر الديناميكية

**الوظيفة:** رفع شروط التداول أو إيقاف الإشارات حسب الأداء الأخير.

**الحالات:**

| الحالة | المعنى |
|---|---|
| NORMAL | تداول طبيعي |
| CAUTION | خسائر حديثة أكثر من الأرباح، رفع شروط الثقة والجودة |
| STRICT | خسارتان متتاليتان، مطلوب ثقة وجودة أعلى |
| HALT | 3 خسائر متتالية، إيقاف مؤقت |
| DAILY_HALT | خسارة يومية تجاوزت الحد |

**يمنع الإشارة عندما:**

- `can_trade = false`.
- ثقة الإشارة أقل من المطلوب ديناميكيًا.
- جودة الإشارة أقل من المطلوب ديناميكيًا.

---

### 11. DecisionAgent — وكيل القرار النهائي

**الوظيفة:** دمج كل الوكلاء وإرسال القرار النهائي إلى Groq، ثم تطبيق الفلاتر النهائية.

**لا يرسل BUY/SELL إلا إذا:**

- Groq متاح ويعمل.
- الإشارة ليست WAIT.
- الثقة فوق الحد الأدنى.
- إدارة المخاطر وافقت.
- الأخبار لا تمنع.
- AI News لا يمنع.
- Daily Bias لا يمنع.
- Dynamic Risk لا يمنع.
- الإشارة ليست مكررة.
- لا توجد قواعد ذاكرة تمنع ضمنيًا القرار عبر Prompt Groq.

**يتحول إلى WAIT عندما:**

- Groq فشل.
- Groq أعطى ثقة منخفضة.
- الأخبار أو الاتجاه أو المخاطر منعت الصفقة.
- القرار عكس Daily Bias بدون ثقة كافية.

---

### 12. TradingSessionAgent — وكيل جلسات التداول

**الوظيفة:** تحديد هل الوقت الحالي داخل نافذة التداول المسموحة.

**الإعداد الحالي:**

```text
الاثنين إلى الجمعة
07:59 صباحًا إلى 18:01 مساءً
Asia/Jerusalem
```

**يسمح بالإشارات عندما:**

- اليوم من الاثنين إلى الجمعة.
- الوقت بين 07:59 و18:01.
- الجلسة تسمح بـ `allow_signals = true`.
- جودة الجلسة لا تقل عن الحد المطلوب.

**يمنع الإشارات عندما:**

- خارج الوقت.
- السبت أو الأحد.
- جلسة تقارير وليست جلسة إشارات.

---

### 13. OpenTradesManager — وكيل متابعة الصفقات المفتوحة

**الوظيفة:** متابعة الصفقات المحفوظة في Supabase/JSON.

**يتابع:**

- الوصول إلى TP1.
- الوصول إلى TP2.
- ضرب SL.
- نقل الوقف إلى Break-even بعد TP1.
- تنبيه قرب TP1.
- انتهاء صلاحية الصفقة.
- الصفقات الطويلة بدون حسم.

**يرسل Telegram عند:**

- تحقق TP1.
- تحقق TP2.
- ضرب SL.
- اقتراب الصفقة من الهدف.
- انتهاء صلاحية الصفقة.

---

### 14. DailyReportAgent — وكيل التقرير اليومي

**الوظيفة:** تلخيص أداء اليوم.

**يعرض:**

- عدد الصفقات.
- الرابحة والخاسرة.
- المفتوحة.
- Net Points.
- Win Rate.
- Profit Factor.

ويعمل ضمن جلسة نهاية اليوم الساعة 23:00 بتوقيتك.

---

### 15. LearningService — وكيل/خدمة التعلم

**الوظيفة:** تحليل أداء الوكلاء وتحديث أوزانهم.

**يتعلم من:**

- الصفقات المغلقة.
- أداء كل وكيل.
- سلسلة الأرباح والخسائر.
- الذاكرة السابقة.

**يحدث:**

```text
agent_weights
learning_history
```

---

### 16. TradeReviewService — مراجعة الخسائر

**الوظيفة:** مراجعة الصفقات الخاسرة بواسطة Groq.

**ينتج:**

- سبب الخسارة.
- تصنيف الخطأ.
- ماذا حدث بشكل خاطئ.
- ماذا كان جيدًا.
- ملاحظات على الوكلاء.
- قواعد مقترحة للتحسين.

ويحفظ النتائج في:

```text
ai_trade_reviews
```

---

### 17. MemoryRules Engine — ذاكرة قواعد التعلم

**الوظيفة:** تحويل مراجعات الخسائر إلى قواعد دائمة.

**مثال قاعدة:**

```text
لا تدخل SELL قرب دعم قوي دون تأكيد إغلاق شمعة 15m.
```

تحفظ في:

```text
ai_memory_rules
```

وتدخل لاحقًا في Prompt قرار Groq.

---

### 18. Dashboard Generator — لوحة المتابعة

**الوظيفة:** إنشاء لوحة HTML تعرض حالة النظام.

تعرض:

- الصفقات.
- Win Rate.
- Net Points.
- Profit Factor.
- مراجعات Groq.
- قواعد الذاكرة النشطة.

---

### 19. Backtesting Engine — الاختبار التاريخي

**الوظيفة:** اختبار الاستراتيجية على بيانات سابقة.

يقيس:

- عدد الصفقات.
- Win Rate.
- Net Points.
- Profit Factor.
- Max Drawdown.
- أداء BUY/SELL.

> لا يستخدم Groq افتراضيًا حتى لا يستهلك API بكثرة.

---

### قاعدة القرار النهائي المختصرة

حتى يتم إرسال إشارة، يجب أن تمر عبر هذه السلسلة:

```text
TradingSession ✅
MarketData ✅
Technical/Classical/SMC/PA/MTF ✅
DailyBias ✅
NewsRisk + AI News ✅
RiskManagement ✅
DynamicRisk ✅
MemoryRules داخل Groq ✅
Groq Decision ✅
Duplicate Filter ✅
Telegram + Supabase ✅
```

إذا فشل شرط مهم، تكون النتيجة:

```text
WAIT
```

أو لا يتم إرسال الإشارة.

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
