# 🏆 Gold AI Signals — نظام إشارات الذهب الذكي

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
- 13 وكيل تحليل فني/سلوكي/مخاطر/إدارة
- Groq Cloud كبوّابة قرار نهائية
- Supabase لتخزين الصفقات والتعلّم المستمر
- Telegram للإشعارات والتقارير
- Backtesting + Dashboard + AI Memory Rules + Trailing Stop

---

## ✅ الحالة الحالية

| الجزء | الحالة |
|---|---|
| Telegram / Groq / GitHub Actions | ✅ يعمل |
| الاختبارات | ✅ **217/217** ناجح |
| تحذيرات `pyflakes` | ✅ **0** تحذير |
| Paper Trading | ✅ مفعّل |
| Groq كقرار نهائي | ✅ إجباري |
| AI Memory Rules / Backtesting / Dashboard / Trailing | ✅ مضاف |

---

## 🧠 كيف يعمل النظام

### وضع التشغيل: One-Agent + Groq
- وكيل واحد كافٍ لتوفير السياق
- **Groq فقط** هو من يقرّر BUY/SELL/WAIT
- إذا Groq فاشل أو يقول WAIT ← الإشارة تُحجب
- Retry: 3 محاولات مع exponential backoff (1s, 2s) لـ transient errors

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
| `NewsRiskAgent` | فحص الأخبار (ForexFactory تلقائي + يدوي اختياري) |
| `TradingSessionAgent` | جودة الجلسة (HIGH/MEDIUM/LOW) |
| `DailyBiasAgent` | الاتجاه اليومي على 4H (EMA/RSI) |
| `RiskManagementAgent` | حساب SL/TP/R:R وحجم الصفقة |

**وكلاء القرار والإدارة (4):**

| الوكيل | الوظيفة |
|---|---|
| `DecisionAgent` | دمج كل النتائج + Groq final gate |
| `OpenTradesManager` | متابعة SL/TP/Trailing للصفقات المفتوحة |
| `DailyReportAgent` | توليد التقرير اليومي |
| `BaseAgent` | البنية المشتركة |

### خط الإشارة (Signal Pipeline)

```
Twelve Data → 5 وكلاء تحليل → جمع الأصوات (مع weights)
    ↓
NewsRisk + TradingSession + DailyBias + RiskManagement
    ↓
DecisionAgent → Groq (إجباري) → BUY / SELL / WAIT
    ↓
DynamicRiskManager (HALT/CAUTION) → Duplicate Filter → Telegram
```

---

## 🛡️ الفلاتر (يجب أن تجتاز الإشارة كلها)

| الفلتر | الشرط |
|---|---|
| **Groq** | متاح ويقول BUY/SELL بثقة ≥ 60 |
| **NewsRisk** | لا توجد أخبار HIGH قبل 60د أو بعد 30د |
| **Duplicate** | لا إشارة مشابهة في آخر 90 دقيقة |
| **Session** | داخل 09:00–22:59 Asia/Hebron |
| **DailyBias** | لا مخالفة قوية للاتجاه اليومي (بدون ثقة ≥ 80) |
| **DynamicRisk** | ليس في HALT (بعد 3 خسائر متتالية) |
| **CAUTION** | ثقة ≥ 75 + جودة ≥ 70 لو في CAUTION |
| **STRICT** | ثقة ≥ 82 + جودة ≥ 80 لو في STRICT |

### درجة جودة الإشارة (A+ / A / B / C / D)
تظهر في Telegram بناءً على: الثقة + توافق الوكلاء + R:R + إدارة المخاطر + الجلسة.

---

## ⏰ أوقات التشغيل (Asia/Hebron)

| المهمة | التوقيت |
|---|---|
| التحليل وإرسال الإشارات | كل 10 دقائق، 09:00–22:59 (أحد–خميس) |
| تحديث الصفقات المفتوحة | كل ساعة، 09:00–22:59 |
| التقرير اليومي + Learning + AI Review | 23:00 يوميًا |

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

| Workflow | الوظيفة | الجدولة |
|---|---|---|
| ✅ Tests | تشغيل 217 اختبار | عند push / PR / يدوي |
| 📊 Gold Analysis Bot | التحليل + إرسال الإشارات | كل 10 دقائق (09:00–22:59) |
| 🔄 Update Open Trades | تحديث SL/TP/Trailing | كل ساعة (09:00–22:59) |
| 📊 Daily Report & Learning | تقرير نهاية اليوم + تعلّم | 23:00 يوميًا |
| 📊 Dashboard | توليد HTML Dashboard | 23:15 يوميًا |
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

- يجلب من `https://nfs.faireconomy.media/ff_calendar_thisweek.xml`
- Cache 30 دقيقة · يُصفّي USD و ALL فقط (التي تخص الذهب)
- يُصنّف تلقائيًا: HIGH (NFP/CPI/FOMC/GDP/Rate Decision/Powell/PCE) / MEDIUM / LOW
- AI يفسر النتيجة ويحدد اتجاه مسموح أو حظر كامل (ai_news_interpretation)

---

## 💰 إدارة المخاطر (3 طبقات)

### 1) RiskManagementAgent (قبل الإشارة)
- حساب SL بـ ATR × 1.5
- TP1 = R:R 2.0 · TP2 = R:R 3.5
- max R:R = 4.0 (سقف لتجنّب أهداف غير واقعية)

### 2) DynamicRiskManager (بعد الإشارة)
| المستوى | شروط التفعيل | التأثير |
|---|---|---|
| **NORMAL** | افتراضي | لا قيود إضافية |
| **CAUTION** | 2 خسارة متتالية | ثقة ≥ 75 + جودة ≥ 70 |
| **STRICT** | warn_after_losses (2) | ثقة ≥ 82 + جودة ≥ 80 |
| **HALT** | 3 خسائر متتالية | حظر كامل |
| **DAILY_HALT** | خسارة يومية ≥ 30 نقطة | حظر كامل |

### 3) Trade Management (أثناء الصفقة)
- Trailing Stop بعد TP1 (15–20 نقطة)
- Partial Close 50% عند TP1
- Auto move SL → entry بعد TP1
- expire_after_hours = 8

---

## 🤖 مزايا الذكاء الاصطناعي

### Groq Integration
- **Retry/Backoff**: 3 محاولات مع exponential (1s, 2s) لأخطاء مؤقتة
- **لا retry لـ**: 401/403/400 (أخطاء المفتاح أو الطلب)
- **الموديل الافتراضي**: `llama-3.3-70b-versatile`
- **3 prompts متخصصة**: analysis · smc · decision
- **JSON parsing**: مع fallback لـ markdown-wrapped JSON

### Groq Observation Mode
- retry_on_contradiction: إعادة طلب Groq مرة واحدة عند تناقض
- block_on_ai_contradiction: حظر الإشارة لو الأدلة متضاربة
- min_supportive_evidence_items: الحد الأدنى من الأدلة المؤيدة

### AI Memory Rules
- استخراج قواعد من Trade Review التلقائي
- يُضاف إلى prompt القرار القادم (max 8 قواعد)
- sanitize_rule_text() يمنع prompt injection
- min_confidence_to_apply = 60
- advisory_mode = true (لا يفرض، يُشير فقط)

### AI Trade Review
- Groq يراجع الصفقات الخاسرة المغلقة
- يولّد دروسًا + قواعد تحسين
- max_reviews_per_run = 3 · recent_trades_limit = 30
- review_only_losses = true

### AI News Interpretation
- Groq يفسر الأحداث الإخبارية
- يحدد هل يُمنع التداول أو يُسمح باتجاه واحد فقط
- block_on_extreme = true

### Learning Service
- تحديث أوزان الوكلاء أسبوعيًا بناءً على الأداء
- min_predictions_for_adjustment = 3
- max_weight_change = 0.25 (حد أقصى للتغيير)
- aggressive_mode = true (تعديل أسرع)
- streak_bonus = 0.1 (مكافأة السلاسل الرابحة)

---

## 📈 Dashboard & Performance

### Dashboard (HTML artifact)
- ملخّص الأسبوع: win rate, PnL, عدد الصفقات
- رسم بياني للأداء اليومي
- أفضل/أسوأ وكيل
- أكبر صفقة رابحة/خاسرة
- توزيع الجلسة (London/NY/Asian)

### Performance Tracking
- track_win_rate_per_agent: ✅
- track_session_performance: ✅
- alert_on_drawdown_percent: 5%
- report_interval_days: 7

### Backtesting
- timeframe: 5m/15m/1H/4H
- rolling window: 160 شمعة
- step: 12 شمعة بين التقييمات
- horizon: 32 شمعة للمحاكاة
- إرسال Telegram تلقائي للنتائج

---

## 📰 الأوامر المتاحة في Telegram

لا يدعم النظام أوامر Telegram تفاعلية حاليًا (يحتاج Webhook أو سيرفر دائم). متاحة في خارطة الطريق.

---

## 🗄️ Supabase Tables

`trades` · `signals` · `agent_weights` · `learning_history` · `agent_evaluations` · `ai_trade_reviews` · `ai_memory_rules` · `portfolio` · `daily_reports` · `news_log` · `session_log` · `risk_settings`

---

## 📁 هيكل المشروع

```
Nabil-gold/
├── .github/workflows/      9 workflows (analyze, tests, daily_report, ...)
├── agents/                 13 ملف (decision, technical, classical, smc, ...)
├── services/               15 ملف (ai_service, database, telegram_bot, ...)
├── scripts/                11 ملف (run_analysis, run_trade_updates, ...)
├── tests/                  18 ملف — 217 اختبار
├── utils/                  helpers + indicators
├── config.json             الإعدادات الرئيسية (38 قسم)
├── supabase_schema.sql     مخطط قاعدة البيانات
├── requirements.txt        requests, pandas, supabase, httpx, pytest
└── main.py                 نقطة دخول محلية
```

---

## 🧪 الاختبارات

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m pytest tests/ -v
```

**النتيجة:** `217 passed` في ~1 ثانية · تغطية ~63% · `pyflakes`: 0 تحذير

---

## 🛠️ التطويرات الأخيرة

### Reliability (P0)
- ✅ Groq retry/backoff (3 محاولات، exponential 1s/2s)
- ✅ Synthetic data guard (يحجب الإنتاج على بيانات وهمية)
- ✅ Supabase strict mode (لا fallback إلى JSON محلي في GitHub Actions)
- ✅ Workflow concurrency (منع تداخل التشغيلات)
- ✅ NEUTRAL/HOLD/NO_TRADE → WAIT (توحيد)

### Quality (P1)
- ✅ Groq Observation Mode: One-Agent + Groq إجباري
- ✅ ForexFactory news feed (مجاني، بدون API key)
- ✅ sanitize_rule_text() (منع prompt injection)
- ✅ Dynamic Risk Manager (HALT بعد 3 خسائر، CAUTION بعد 2)
- ✅ Duplicate Signal Filter (نافذة 90 دقيقة)
- ✅ AI Memory Rules من Trade Review
- ✅ Trailing Stop بعد TP1 + Partial Close 50%
- ✅ Agent Playbooks في prompt Groq (v3.0)
- ✅ Daily Bias Filter (EMA/RSI على 4H)
- ✅ Signal Quality Score (A+/A/B/C/D)

### Code Quality
- ✅ **217/217** اختبار ناجح
- ✅ **0** تحذير `pyflakes` (كان 27)
- ✅ NameError الحرج في `_final_decision` مُصلَح
- ✅ الكود الميت في `decision_agent.py` محذوف

---

## 🧭 خارطة الطريق القادمة

| الأولوية | الميزة | الوصف |
|---|---|---|
| ⭐⭐⭐ | **Weekly AI Performance Report** | تقرير يكتبه Groq كل أحد 23:30 |
| ⭐⭐ | Telegram Commands | `/status`, `/open`, `/report`, `/pause` |
| ⭐⭐ | GitHub Pages Dashboard | رابط دائم بدل Artifact |
| ⭐ | Backtest مع Groq اختياريًا | تحليل جودة قرارات Groq تاريخيًا |

📄 التصميم التفصيلي للتقرير الأسبوعي في [`WEEKLY_REPORT_PROPOSAL.md`](./WEEKLY_REPORT_PROPOSAL.md).

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**

</div>
