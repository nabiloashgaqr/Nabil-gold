# 🏆 Gold AI Signals — نظام إشارات الذهب الذكي

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals-blue)
![Groq](https://img.shields.io/badge/AI-GroqCloud-purple)
![Tests](https://img.shields.io/badge/Tests-245%20Passed-brightgreen)
![Mode](https://img.shields.io/badge/Mode-Paper%20Trading-yellow)

</div>

---

## ⚠️ تنبيه

المشروع **تعليمي/تجريبي** لإشارات الذهب **XAU/USD** — لا يُعد توصية مالية.
الوضع الحالي: **Paper Trading** (صفقات افتراضية، بدون تنفيذ حقيقي).

---

## 🎯 نظرة عامة

نظام آلي لتحليل الذهب وإرسال إشارات إلى **Telegram**، يعمل بالكامل على **GitHub Actions** بوضع **One-Agent + Groq** (قرار Groq إجباري).

**المكوّنات الأساسية:**
- بيانات سوق XAU/USD لحظية (Twelve Data) + حارس بيانات وهمية (synthetic guard) في الإنتاج
- 13 وكيل تحليل فني/سلوكي/مخاطر/إدارة
- Groq Cloud كبوّابة قرار نهائية، مع retry/backoff للأخطاء العابرة
- Supabase لتخزين الصفقات والإشارات والتعلّم المستمر (13 جدول)
- Telegram للإشعارات والتقارير (يومي + أسبوعي بالذكاء الاصطناعي)
- حلقة تعلّم كاملة: AI Trade Review (Groq) + Learning Service (إحصائي) → كلاهما يغذّي القرار التالي فعلياً
- Backtesting + Dashboard HTML + AI Memory Rules + Trailing Stop تقدّمي حقيقي

---

## ✅ الحالة الحالية

| الجزء | الحالة |
|---|---|
| Telegram / Groq / GitHub Actions | ✅ يعمل |
| الاختبارات | ✅ **245/245** ناجح |
| Paper Trading | ✅ مفعّل |
| Groq كقرار نهائي (One-Agent + Groq) | ✅ إجباري |
| حلقة التعلّم (Memory Rules + Learning Weights) | ✅ متصلة بالكامل بقاعدة البيانات |
| Trailing Stop التقدّمي | ✅ حقيقي ومُفعّل على الصفقات الفعلية |
| **وضع التشغيل الحالي** | 🟡 **مرحلة تعلّم** — قيود الأداء التاريخي مرفوعة مؤقتاً (انظر أدناه) |

### 🟡 إعدادات "مرحلة التعلّم" الحالية (مؤقتة، قابلة للتشديد لاحقاً)

| الإعداد | القيمة الحالية | الافتراضي الأصلي |
|---|---|---|
| `risk_settings.max_open_trades` | 50 | 3 |
| `risk_settings.max_daily_signals` | 50 | 8 |
| `filters.max_consecutive_losses` | 999 (معطّل فعليًا) | 3 |
| `dynamic_risk_management.enabled` | `false` (HALT/STRICT/CAUTION/DAILY_HALT كلها معطّلة) | `true` |
| `risk_settings.min_confidence` / `groq_observation_mode.min_groq_confidence` | 60% (بدون تغيير) | 60% |
| `ai_trade_review.max_reviews_per_run` / `recent_trades_limit` | 20 / 60 (رُفعت لتستوعب حجم الإشارات الأعلى) | 3 / 30 |

> الهدف: ترك كل الإشارات (ثقة ≥ 60%) تخرج بدون إيقاف بسبب أداء سابق، لتغذية حلقة Memory Rules + Learning Weights ببيانات كافية، ثم التشديد تدريجيًا.

---

## 🧠 كيف يعمل النظام

### وضع التشغيل: One-Agent + Groq
- وكيل واحد كافٍ لتوفير السياق (`min_agents_agree: 1`)
- **Groq فقط** هو من يقرّر BUY/SELL/WAIT النهائي
- إذا Groq فاشل أو يقول WAIT ← الإشارة تُحجب
- Retry: 3 محاولات مع exponential backoff (1s, 2s) على timeout/connection/429/5xx — لا retry على 401/403/400

### الوكلاء (13 وكيل)

**وكلاء التحليل (5):**

| الوكيل | الوظيفة |
|---|---|
| `TechnicalAgent` | RSI / MACD / EMA / ATR |
| `ClassicalAgent` | أنماط كلاسيكية + دعم/مقاومة |
| `SMCAgent` | Smart Money (Order Blocks / Liquidity / FVG) |
| `PriceActionAgent` | قراءة حركة السعر + الشموع |
| `MultiTimeframeAgent` | مقارنة الاتجاه عبر 5m/15m/1H/4H |

**وكلاء الفلترة والسياق (4):**

| الوكيل | الوظيفة |
|---|---|
| `NewsRiskAgent` | فحص الأخبار (ForexFactory تلقائي + يدوي اختياري) — نص الأحداث مُعقَّم ضد prompt injection |
| `TradingSessionAgent` | جودة الجلسة (HIGH/MEDIUM/LOW) |
| `DailyBiasAgent` | الاتجاه اليومي على 4H (EMA/RSI) |
| `RiskManagementAgent` | حساب SL/TP/R:R وحجم الصفقة |

**وكلاء القرار والإدارة (4):**

| الوكيل | الوظيفة |
|---|---|
| `DecisionAgent` | دمج كل النتائج + أوزان متعلّمة من DB + Groq final gate |
| `OpenTradesManager` | متابعة SL/TP/Breakeven/Trailing التقدّمي للصفقات المفتوحة |
| `DailyReportAgent` | توليد التقرير اليومي |
| `BaseAgent` | البنية المشتركة |

### خط الإشارة (Signal Pipeline)

```
Twelve Data → 5 وكلاء تحليل → جمع الأصوات (بأوزان مُحدَّثة من DB يوميًا)
    ↓
NewsRisk (مُعقَّم) + TradingSession + DailyBias + RiskManagement
    ↓
DecisionAgent → Groq (إجباري، مع retry) → BUY / SELL / WAIT
    ↓
DynamicRiskManager (معطّل حاليًا/مرحلة تعلّم) → Duplicate Filter → Telegram
```

---

## 🛡️ الفلاتر (يجب أن تجتاز الإشارة كلها)

| الفلتر | الشرط | الحالة الآن |
|---|---|---|
| **Groq** | متاح ويقول BUY/SELL بثقة ≥ 60% | ✅ فعّال |
| **NewsRisk** | لا توجد أخبار HIGH قبل 60د أو بعد 30د | ✅ فعّال |
| **Duplicate** | لا إشارة مشابهة في آخر 90 دقيقة | ✅ فعّال |
| **Session** | داخل 09:00–22:59 Asia/Hebron | ✅ فعّال |
| **DailyBias** | لا مخالفة قوية للاتجاه اليومي (بدون ثقة ≥ 80) | ✅ فعّال |
| **DynamicRisk (HALT/CAUTION/STRICT)** | بعد خسائر متتالية/يومية | 🟡 معطّل (مرحلة تعلّم) |
| **حد الصفقات المفتوحة/اليومية** | max_open_trades / max_daily_signals | 🟡 مرفوع إلى 50 |
| **خسائر متتالية (فلتر منفصل)** | max_consecutive_losses | 🟡 مرفوع إلى 999 |

### درجة جودة الإشارة (A+ / A / B / C / D)
تظهر في Telegram بناءً على: الثقة + توافق الوكلاء + R:R + إدارة المخاطر + الجلسة.

---

## 💰 إدارة المخاطر (3 طبقات)

### 1) RiskManagementAgent (قبل الإشارة) — فعّال دائمًا
- حساب SL بـ ATR × 1.5
- TP1 = R:R 2.0 · TP2 = R:R 3.5
- max R:R = 4.0 (سقف لتجنّب أهداف غير واقعية)

### 2) DynamicRiskManager (بعد الإشارة) — معطّل مؤقتًا (مرحلة تعلّم)
| المستوى | شروط التفعيل | التأثير المصمَّم |
|---|---|---|
| **NORMAL** | افتراضي | لا قيود إضافية |
| **CAUTION** | خسارتان متتاليتان (recent_losses) | ثقة ≥ 75 + جودة ≥ 70 |
| **STRICT** | warn_after_losses (2) | ثقة ≥ 82 + جودة ≥ 80 |
| **HALT** | 3 خسائر متتالية | حظر كامل |
| **DAILY_HALT** | خسارة يومية ≥ 30 نقطة | حظر كامل |

> الكود مغطّى بالكامل بالاختبارات (16 اختبار في `test_dynamic_risk.py`)، جاهز لإعادة التفعيل بتغيير `enabled: true` فقط.

### 3) Trade Management (أثناء الصفقة) — فعّال دائمًا
- Partial Close 50% عند TP1 + نقل SL إلى نقطة الدخول فعليًا (يُكتب في DB، لا يبقى علماً فقط)
- **Trailing Stop تقدّمي حقيقي** بعد قفل Breakeven: يتحرك فقط بالاتجاه الرابح، لا يتراجع أبدًا عند الانسحاب
- إغلاق `TRAILING_SL_HIT` كـ WIN بالربح المقفول الفعلي (لا كـ Breakeven عادي)
- `expire_after_hours = 8`

---

## 🤖 مزايا الذكاء الاصطناعي

### Groq Integration
- **Retry/Backoff**: 3 محاولات مع exponential (1s, 2s) لـ timeout/connection/429/5xx
- **لا retry لـ**: 401/403/400 (أخطاء المفتاح أو الطلب — فشل فوري بدون هدر وقت)
- **الموديل الافتراضي**: `llama-3.3-70b-versatile`
- **مفتاح API**: يدعم مؤشر `ENV:VAR_NAME` في config.json (يُحلّ تلقائيًا لكل المزودين بما فيهم Groq)
- **3 prompts متخصصة**: analysis · smc · decision
- **JSON parsing**: مع fallback لـ markdown-wrapped JSON

### Groq Observation Mode
- retry_on_contradiction: إعادة طلب Groq مرة واحدة عند تناقض
- block_on_ai_contradiction: حظر الإشارة لو الأدلة متضاربة
- min_supportive_evidence_items: الحد الأدنى من الأدلة المؤيدة

### AI Memory Rules
- استخراج قواعد من Trade Review التلقائي
- يُضاف إلى prompt القرار القادم (max 8 قواعد)
- **`sanitize_prompt_text()`**: يمنع prompt injection — مُطبَّق على نص قواعد الذاكرة **وعلى عناوين/تفاصيل الأخبار** (ForexFactory + يدوي) منذ المصدر مباشرة
- min_confidence_to_apply = 60 · advisory_mode = true (لا يفرض، يُشير فقط)

### AI Trade Review (مغذّي حلقة Memory Rules)
- Groq يراجع الصفقات الخاسرة المغلقة فعليًا كل يوم (20:00 UTC)
- يولّد دروسًا + قواعد تحسين تُحفظ في `ai_memory_rules`
- `max_reviews_per_run = 20` · `recent_trades_limit = 60` · `review_only_losses = true`

### AI News Interpretation
- Groq يفسر الأحداث الإخبارية (نص مُعقَّم مسبقًا)
- يحدد هل يُمنع التداول أو يُسمح باتجاه واحد فقط
- `block_on_extreme = true`

### Learning Service (إحصائي) — متصل الآن بالكامل بالقرار الفعلي ✅
- يحسب أوزان جديدة للوكلاء يوميًا بناءً على الأداء الفعلي ويحفظها في جدول `agent_weights`
- **`DecisionAgent.analyze_async`** يقرأ هذه الأوزان من DB في بداية كل تحليل (كانت سابقًا تُقرأ من `config.json` الثابت فقط — تم إصلاح الانقطاع الكامل بين الحلقتين)
- fallback تلقائي لأوزان config.json لو فشل الاتصال بقاعدة البيانات أو لم توجد بيانات بعد
- `max_weight_change = 0.25` · `aggressive_mode = true` · `streak_bonus = 0.1`

---

## 📊 Weekly AI Performance Report

تقرير أسبوعي يكتبه Groq تلقائيًا ويرسله على Telegram.

**كيف يعمل:**
```
[الأحد 23:30 Asia/Hebron] → جمع بيانات آخر 7 أيام (trades, session_log, ai_memory_rules, signals المحظورة)
    ↓ بناء Prompt بالبيانات الفعلية → Groq يكتب تقرير Markdown بالعربية
    ↓ تقسيم تلقائي لو > 4096 حرف (حد Telegram) → إرسال + حفظ في storage/weekly_report.json
```

**يتضمن:** ملخص الأداء (صفقات/win rate/PnL) · أداء كل وكيل (أفضل/أسوأ) · أفضل/أسوأ يوم · أداء الجلسات (London/NY/Asian) · تنبيهات HALT/CAUTION · عدد قواعد الذاكرة الجديدة · توصيات قابلة للتنفيذ (لا تُطبَّق تلقائيًا، تحتاج مراجعة بشرية).

**Fallback:** أسبوع هادئ (< `min_trades_for_report`=5) → رسالة بدون توصيات · Groq يفشل → ملخص آلي · Telegram يفشل → يُحفظ JSON فقط.

**التكلفة:** ~$0.0016/أسبوع · **الإعدادات:** `config.json → weekly_report` · **الاختبارات:** 17 اختبار في `test_weekly_report.py`.

---

## 📈 Dashboard & Performance & Backtesting

### Dashboard (HTML artifact، يوميًا 23:15)
ملخّص الأسبوع (win rate, PnL, عدد الصفقات) · رسم بياني للأداء اليومي · أفضل/أسوأ وكيل · أكبر صفقة رابحة/خاسرة · توزيع الجلسات

### Performance Tracking
`track_win_rate_per_agent` · `track_session_performance` · `alert_on_drawdown_percent: 5%` · `report_interval_days: 7`

### Backtesting (يدوي)
timeframe 5m/15m/1H/4H · rolling window 160 شمعة · step 12 شمعة · horizon 32 شمعة · إرسال Telegram تلقائي للنتائج

---

## ⏰ أوقات التشغيل (Asia/Hebron)

| المهمة | التوقيت |
|---|---|
| التحليل وإرسال الإشارات | كل 10 دقائق، 09:00–22:59 (أحد–خميس) |
| تحديث الصفقات المفتوحة (+ Trailing Stop) | كل ساعة، 09:00–22:59 |
| التقرير اليومي + Learning + AI Trade Review | 23:00 يوميًا |
| Dashboard HTML | 23:15 يوميًا |
| Weekly AI Performance Report | الأحد 23:30 |

---

## 🧾 GitHub Actions (10 workflows)

| Workflow | الوظيفة | الجدولة |
|---|---|---|
| ✅ Tests | تشغيل 245 اختبار | عند push / PR / يدوي |
| 📊 Gold Analysis Bot (`analyze.yml`) | التحليل + إرسال الإشارات | كل 10 دقائق (09:00–22:59) |
| 🔄 Update Open Trades (`update_trades.yml`) | SL/TP/Breakeven/Trailing | كل ساعة (09:00–22:59) |
| 📊 Daily Report & Learning (`daily_report.yml`) | تقرير + Learning + AI Trade Review | 23:00 يوميًا |
| 📊 Dashboard (`dashboard.yml`) | توليد HTML Dashboard | 23:15 يوميًا |
| 📊 Weekly Report (`weekly_report.yml`) | تقرير Groq الأسبوعي | الأحد 23:30 |
| 📱 Telegram Smoke Test | فحص Telegram | يدوي |
| 🤖 Groq Smoke Test | فحص Groq API | يدوي |
| 🧪 Backtest | اختبار تاريخي | يدوي |
| 🧪 Groq Model Compare | مقارنة نماذج Groq | يدوي |

---

## 📊 الأخبار — تلقائي ومجاني

| المصدر | النوع | الأولوية |
|---|---|---|
| 🟢 **ForexFactory** | تلقائي، مجاني | 4 (fallback/إضافي) |
| 🟡 `NEWS_EVENTS_JSON` (env) | يدوي (تجاوز) | 1 (أعلى أولوية) |
| 🟡 `news_events` في config.json | يدوي (تجاوز) | 2 |
| 🟡 `storage/news_events.json` | يدوي (تجاوز) | 3 |

- يجلب من `https://nfs.faireconomy.media/ff_calendar_thisweek.xml` · Cache 30 دقيقة
- يُصفّي USD و ALL فقط (التي تخص الذهب) · يُصنّف تلقائيًا: HIGH (NFP/CPI/FOMC/GDP/Rate Decision/Powell/PCE) / MEDIUM / LOW
- **كل نص حدث (عنوان/forecast/previous) من أي مصدر يُعقَّم في نقطة تجميع واحدة** قبل دخوله أي Prompt لـ Groq
- AI يفسر النتيجة ويحدد اتجاه مسموح أو حظر كامل (`ai_news_interpretation`)

---

## 🗄️ Supabase Tables (13)

`trades` · `signals` · `agent_weights` · `learning_history` · `agent_evaluations` · `ai_trade_reviews` · `ai_memory_rules` · `portfolio` · `daily_reports` · `news_log` · `session_log` · `risk_settings` · `weekly_reports`

---

## 📁 هيكل المشروع

```
Nabil-gold/
├── .github/workflows/      10 workflows (analyze, tests, daily_report, weekly_report, ...)
├── agents/                 13 وكيل (decision, technical, classical, smc, open_trades_manager, ...)
├── services/               15 ملف (ai_service, database, telegram_bot, weekly_report, news_feed_forexfactory, ...)
├── scripts/                12 ملف (run_analysis, run_trade_updates, run_learning, run_weekly_report, ...)
├── tests/                  20 ملف — 245 اختبار
├── utils/                  helpers + indicators
├── config.json             الإعدادات الرئيسية
├── supabase_schema.sql     مخطط قاعدة البيانات (13 جدول)
├── requirements.txt        مكتبات أساسية (openai/anthropic في requirements-optional.txt)
└── main.py                 نقطة دخول محلية
```

---

## 🚀 التشغيل والنشر

### 1) أضف GitHub Secrets
من: `Repository → Settings → Secrets and variables → Actions → New repository secret`

| Secret | مطلوب | ملاحظات |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | نعم | توكن بوت Telegram من BotFather |
| `TELEGRAM_CHAT_ID` | نعم | رقم المحادثة/القناة |
| `SUPABASE_URL` | نعم | Project URL من Supabase |
| `SUPABASE_KEY` | نعم | يفضّل **Service Role Key** وليس anon key |
| `TWELVE_DATA_API_KEY` | نعم | إلزامي — بدونه يفشل Workflow التحليل عمدًا لحمايتك من إشارات بيانات وهمية |
| `GROQ_API_KEY` | نعم عمليًا | بوّابة القرار النهائية في الوضع الحالي |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` | اختياري | مزودون بدلاء فقط |

### 2) شغّل مخطط قاعدة البيانات
افتح `supabase_schema.sql` في **Supabase SQL Editor** ونفّذه كاملًا، وتأكد من وجود الجداول (13، انظر القائمة أعلاه).

### 3) فحص الإعدادات
كل Workflow يحتوي خطوة:
```bash
python scripts/validate_setup.py analyze        # أو update-trades / daily-report
```

### 4) شغّل بالترتيب (يدويًا أول مرة، من تبويب Actions)
1. **✅ Tests**
2. **📱 Telegram Smoke Test**
3. **🤖 Groq Smoke Test**
4. **📊 Daily Report & Learning**
5. **🔄 Update Open Trades**
6. **📊 Gold Analysis Bot**

### 5) نقاط أمان مهمة
- لا ترسل أي Personal Access Token أو مفتاح API في المحادثات أو README أو أي ملف يُرفع للريبو
- استخدم Supabase **Service Role Key** فقط داخل GitHub Secrets، وليس داخل الكود
- لا تفعّل `allow_synthetic_in_production` إلا للاختبار المحلي فقط
- ابدأ Paper Trading لمدة 2–4 أسابيع على الأقل قبل أي اعتماد فعلي (وراجع جدول "مرحلة التعلّم" أعلاه)
- بعد استخدام أي Personal Access Token مؤقت لرفع تعديلات، احذفه (Revoke) من GitHub فورًا

### 6) متابعة الصفقات المفتوحة خارج ساعات التحليل
```json
"trade_management": { "update_outside_trading_hours": true }
```
يسمح بمتابعة/تحديث الصفقات المفتوحة (SL/TP/Trailing) حتى خارج ساعات توليد الإشارات.

---

## 🧪 الاختبارات

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

**النتيجة:** `245 passed` في ~1.5 ثانية

---

## 🛠️ آخر التطويرات (سجل مختصر)

### إصلاحات موثوقية وأمان
- ✅ إصلاح باغ مؤشر `ENV:` في `ai_service.py` (كان يستخدم النص الحرفي `ENV:VAR` كمفتاح API بدل تحليله)
- ✅ Groq retry/backoff (3 محاولات، exponential 1s/2s) على timeout/429/5xx — لا retry على 401/403/400
- ✅ تعقيم نص الأخبار (`sanitize_prompt_text`) قبل دخوله أي Groq prompt — منبع موحّد لكل المصادر
- ✅ Synthetic data guard (يحجب الإنتاج على بيانات وهمية) · Supabase strict mode

### إصلاح حلقة التعلّم المقطوعة (الأهم)
- ✅ `run_learning.py` كان يحسب ويحفظ أوزان جديدة يوميًا في DB، لكن `DecisionAgent` لم يكن يقرأها أبدًا — أُصلح بالكامل، الأوزان الآن تُحمَّل من DB فعليًا قبل كل قرار
- ✅ رفع `ai_trade_review.max_reviews_per_run` (3→20) و `recent_trades_limit` (30→60) لمنع تراكم صفقات غير مراجَعة

### تنظيف كود ميت
- ✅ حذف `services/trailing_stop.py` بالكامل (كان مكتوبًا بحقول غير موجودة في سكيمة الصفقات الفعلية، ولا يُستدعى من أي مكان في خط الإنتاج)
- ✅ تطبيق Trailing Stop تقدّمي **حقيقي** بديل داخل `OpenTradesManager` (يتكامل مع الحقول الفعلية: tp1/tp2, status, sl_moved_to_entry)
- ✅ إصلاح باغ مرافق: نقل SL لنقطة الدخول بعد TP1 كان "علمًا" فقط دون أن يُكتب فعليًا في عمود `stop_loss`
- ✅ إزالة الكود التجريبي `experimental_single_agent` بالكامل من الكود والإعدادات (Fix Pack v2)
- ✅ توحيد كل ملفات README/CHANGES/DEPLOYMENT_CHECKLIST/WEEKLY_REPORT_README المتفرقة في ملف واحد شامل

### ميزات Fix Pack v1
- ✅ ForexFactory news feed (مجاني، بدون API key) كمصدر تلقائي
- ✅ `sanitize_rule_text()` لقواعد الذاكرة (Memory Rules) — قبل تعميمه على الأخبار أيضًا
- ✅ Dynamic Risk Manager (HALT بعد 3 خسائر، CAUTION بعد 2) — مغطّى الآن بـ 16 اختبار (كان 0)
- ✅ Duplicate Signal Filter (نافذة 90 دقيقة) · AI Memory Rules من Trade Review

### Code Quality
- ✅ **245/245** اختبار ناجح (يشمل اختبارات جديدة لـ: ai_service ENV fix، dynamic_risk، Groq retry، تعقيم الأخبار، أوزان DB، Trailing Stop الحقيقي)
- ✅ NameError الحرج في `_final_decision` مُصلَح (كان يحدث عند نجاح استدعاء AI)

---

## 🧭 خارطة الطريق القادمة

| الأولوية | الميزة | الوصف |
|---|---|---|
| ⭐⭐ | تشديد مرحلة التعلّم تدريجيًا | إعادة تفعيل DynamicRisk وخفض حدود الصفقات بعد جمع بيانات كافية |
| ⭐⭐ | Telegram Commands | `/status`, `/open`, `/report`, `/pause` (يحتاج Webhook أو سيرفر دائم) |
| ⭐⭐ | GitHub Pages Dashboard | رابط دائم بدل Artifact |
| ⭐ | Backtest مع Groq اختياريًا | تحليل جودة قرارات Groq تاريخيًا |

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**

</div>
