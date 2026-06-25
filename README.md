# 🏆 Gold AI Signals — نظام إشارات الذهب الذكي

<div align="center">

![Gold](https://img.shields.io/badge/XAU/USD-Gold-FFD700)
![Python](https://img.shields.io/badge/Python-3.13+-green)
![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Automated-orange)
![Telegram](https://img.shields.io/badge/Telegram-Signals%2BReports-blue)
![Groq](https://img.shields.io/badge/AI-GroqCloud-purple)
![Tests](https://img.shields.io/badge/Tests-248%20Passed-brightgreen)
![Mode](https://img.shields.io/badge/Mode-Paper%20Trading-yellow)
![Status](https://img.shields.io/badge/Status-Learning%20Phase-orange)

</div>

---

## ⚠️ تنبيه هام

**المشروع تعليمي / تجريبي فقط** لتداول الذهب **XAU/USD**.  
**لا يُعد توصية مالية أو استثمارية بأي شكل من الأشكال.**

الوضع الحالي: **Paper Trading** (صفقات افتراضية — لا يتم تنفيذ أوامر حقيقية على أي حساب).

---

## 📋 جدول المحتويات

- [نظرة عامة](#-نظرة-عامة)
- [الحالة الحالية](#-الحالة-الحالية)
- [آخر الإصلاحات الرئيسية](#-آخر-الإصلاحات-الرئيسية)
- [كيف يعمل النظام](#-كيف-يعمل-النظام)
- [الفلاتر والحماية](#-الفلاتر-والحماية)
- [إدارة المخاطر](#-إدارة-المخاطر)
- [ميزات الذكاء الاصطناعي](#-ميزات-الذكاء-الاصطناعي)
- [التقارير والداشبورد](#-التقارير-والداشبورد)
- [أوقات التشغيل](#-أوقات-التشغيل)
- [هيكل المشروع](#-هيكل-المشروع)
- [التشغيل والنشر](#-التشغيل-والنشر)
- [الاختبارات](#-الاختبارات)
- [خارطة الطريق](#-خارطة-الطريق)

---

## 🎯 نظرة عامة

نظام آلي متقدم لتحليل سوق الذهب **XAU/USD** وإرسال إشارات احترافية إلى **Telegram**، يعمل بالكامل عبر **GitHub Actions** في وضع **One-Agent + Groq** (قرار Groq إجباري ونهائي).

**المميزات الأساسية:**
- بيانات لحظية من Twelve Data + حماية ضد البيانات الوهمية
- 13 وكيل ذكاء اصطناعي متخصص (فني + سلوكي + مخاطر + إدارة)
- Groq Cloud كبوابة قرار نهائية مع آلية إعادة محاولة ذكية
- قاعدة بيانات Supabase (13 جدول) + حلقة تعلم مستمرة كاملة
- تقارير يومية وأسبوعية عالية الجودة (منظمة + بدون تكرار)
- Trailing Stop تقدمي حقيقي + إدارة صفقات مفتوحة متقدمة
- Dashboard HTML احترافي + Backtesting + Memory Rules

---

## ✅ الحالة الحالية

| المكون                        | الحالة          |
|-------------------------------|-----------------|
| Telegram + Groq + GitHub Actions | ✅ يعمل باستقرار |
| الاختبارات                    | ✅ **248/248** ناجح |
| Paper Trading                 | ✅ مفعّل       |
| Groq كقرار نهائي (One-Agent + Groq) | ✅ إجباري     |
| حلقة التعلم (Memory Rules + Learning Weights) | ✅ متصلة بالكامل |
| Trailing Stop التقدمي         | ✅ حقيقي وفعّال |
| **وضع التشغيل**               | 🟡 **مرحلة التعلم** (مؤقتة) |

### 🟡 إعدادات مرحلة التعلم الحالية (مؤقتة)

| الإعداد                              | القيمة الحالية | القيمة الافتراضية |
|--------------------------------------|----------------|-------------------|
| `max_open_trades`                    | 50             | 3                 |
| `max_daily_signals`                  | 50             | 8                 |
| `max_consecutive_losses`             | 999            | 3                 |
| `dynamic_risk_management.enabled`    | `false`        | `true`            |
| `min_confidence`                     | 60%            | 60%               |
| `ai_trade_review.max_reviews_per_run`| 20             | 3                 |

> **الهدف:** جمع بيانات كافية لتغذية نظام التعلم قبل التشديد التدريجي.

---

## 🛠️ آخر الإصلاحات الرئيسية (يونيو 2026)

### ✅ إصلاح مشكلة "Profit Factor: 0" (الأهم)
- **المشكلة:** عند 8 صفقات رابحة (100% Win Rate) و +3250.3 نقطة صافية → كان يظهر **Profit Factor: 0**.
- **السبب الجذري:** قسمة على `gross_loss = 0` في `dashboard.py` و `daily_report_agent.py` و `weekly_report.py`.
- **الحل:** 
  - حساب داخلي: `99.9` عند عدم وجود خسائر.
  - عرض احترافي: **`∞`** مع ملاحظة توضيحية.
- **التأثير:** الداشبورد + التقرير اليومي + التقرير الأسبوعي + Telegram كلها تعرض القيمة الصحيحة الآن.

### ✅ إعادة تنظيم شاملة لكل التقارير (Telegram)
- إزالة التكرار والأقسام المكررة.
- إزالة أسباب غير منطقية وتعليقات قديمة.
- هيكل موحد واحترافي:
  - **Statistics** → **Performance** (مع PF + ملاحظة)
  - **Direction** → **Best Sources** → **Recommendations** (محدودة)
  - **Data Quality Note** (جديد)
- التقرير اليومي المدمج أصبح أكثر تنظيماً ووضوحاً.
- Weekly Report أصبح يدعم Profit Factor بشكل كامل.

**المرجع الكامل:** [`FIXES_PROFIT_FACTOR.md`](FIXES_PROFIT_FACTOR.md)

---

## 🧠 كيف يعمل النظام

### وضع التشغيل: One-Agent + Groq (الأساسي)
- وكيل واحد كافٍ لتوفير السياق.
- **Groq فقط** يتخذ القرار النهائي (BUY / SELL / WAIT).
- إذا فشل Groq أو قال WAIT → الإشارة تُحجب.
- آلية إعادة محاولة ذكية (3 محاولات + exponential backoff).

### الوكلاء (13 وكيل)

**وكلاء التحليل (5)**
- `TechnicalAgent` — مؤشرات فنية
- `ClassicalAgent` — أنماط كلاسيكية
- `SMCAgent` — Smart Money Concepts
- `PriceActionAgent` — حركة السعر والشموع
- `MultiTimeframeAgent` — تحليل متعدد الإطارات

**وكلاء الفلترة والسياق (4)**
- `NewsRiskAgent`, `TradingSessionAgent`, `DailyBiasAgent`, `RiskManagementAgent`

**وكلاء القرار والإدارة (4)**
- `DecisionAgent`, `OpenTradesManager`, `DailyReportAgent`, `BaseAgent`

### خط الإشارة
```
Market Data → Agents Analysis → Voting + Weights (من DB)
    ↓
Filters (News + Session + Bias + Risk + Duplicate)
    ↓
DecisionAgent → Groq (إجباري) → Signal
    ↓
Duplicate Filter → Telegram + Save to Supabase
```

---

## 🛡️ الفلاتر والحماية

| الفلتر                    | الشرط                              | الحالة     |
|---------------------------|------------------------------------|------------|
| Groq Decision             | يقول BUY/SELL بثقة ≥ 60%           | ✅ فعّال   |
| NewsRisk                  | لا أخبار HIGH قبل/بعد النافذة     | ✅ فعّال   |
| Duplicate Signal          | لا إشارة مشابهة في آخر 90 دقيقة   | ✅ فعّال   |
| Trading Session           | داخل ساعات التداول                 | ✅ فعّال   |
| Daily Bias                | لا مخالفة قوية للاتجاه اليومي     | ✅ فعّال   |
| Dynamic Risk (HALT/CAUTION) | بعد خسائر متتالية               | 🟡 معطّل (مرحلة تعلم) |

---

## 💰 إدارة المخاطر (ثلاث طبقات)

### 1. RiskManagementAgent (قبل الدخول)
- حد أدنى لـ SL = **200 نقطة (20$)**.
- إعادة حساب TP1/TP2 تلقائياً بنفس نسبة R:R.
- ATR-based targets (TP1=×2.0، TP2=×3.5).

### 2. Dynamic Risk (بعد الإشارة) — معطّل مؤقتاً
- CAUTION / STRICT / HALT / DAILY_HALT (جاهز للتفعيل).

### 3. Trade Management (أثناء الصفقة) — فعّال دائماً
- Partial Close 50% عند TP1 + نقل SL إلى نقطة الدخول.
- **Trailing Stop تقدمي حقيقي**: نقل SL للدخول بعد +100 نقطة، ثم Trailing بمسافة 100 نقطة وخطوة 30 نقطة مع إرسال تحديث Telegram عند كل نقل.
- انتهاء تلقائي بعد 24 ساعة (مع استثناءات للصفقات الرابحة).

---

## 🤖 ميزات الذكاء الاصطناعي

- **Groq** كمحرك رئيسي + 3 prompts متخصصة.
- **AI Memory Rules**: استخراج قواعد من مراجعة الصفقات الخاسرة.
- **AI Trade Review**: مراجعة يومية تلقائية للخسائر.
- **Learning Service**: تحديث أوزان الوكلاء يومياً من قاعدة البيانات.
- تعقيم كامل للنصوص (ضد Prompt Injection).

---

## 📊 التقارير والداشبورد

### التقرير اليومي (مدمج)
- أداء اليوم + الصفقات المغلقة + المفتوحة.
- Profit Factor مع عرض `∞` عند عدم وجود خسائر.
- ملاحظات Data Quality + توصيات محدودة ومنظمة.

### التقرير الأسبوعي (Groq)
- يُرسل كل سبت 10:00 صباحاً بتوقيتك المحلي.
- يحتوي الآن على Profit Factor بشكل صحيح.

### Dashboard HTML
- يُولد يومياً كـ GitHub Artifact.
- يعرض Profit Factor كـ `∞` عند الاقتضاء.
- جداول + بطاقات + قواعد الذاكرة + مراجعات الذكاء الاصطناعي.

---

## ⏰ أوقات التشغيل (Asia/Hebron)

| المهمة                        | التوقيت                     |
|-------------------------------|-----------------------------|
| التحليل والإشارات             | كل 10 دقائق (09:00–22:59)   |
| تحديث الصفقات المفتوحة        | كل 5 دقائق (إجباري للأحداث: نقل SL / Trailing / TP / SL) |
| التقرير اليومي + Learning     | 23:00 بتوقيتك المحلي       |
| Dashboard                     | 23:15 يومياً                |
| Weekly Report                 | السبت 10:00 صباحاً بتوقيتك المحلي |

---

## 📁 هيكل المشروع

```
Nabil-gold/
├── .github/workflows/     # 10 GitHub Actions
├── agents/                # 13 وكيل
├── services/              # 15 خدمة (AI, DB, Telegram, Reports...)
├── scripts/               # أدوات التشغيل
├── tests/                 # 248 اختبار
├── config.json            # الإعدادات الرئيسية
├── supabase_schema_unified.sql
└── storage/               # التقارير والداشبورد
```

---

## 🚀 التشغيل والنشر

1. أضف الأسرار في GitHub (TELEGRAM, SUPABASE, GROQ, TWELVE_DATA).
2. نفّذ سكريبت قاعدة البيانات.
3. شغّل الـ Workflows بالترتيب (Tests → Smoke Tests → Daily Report...).
4. ابدأ بـ Paper Trading لعدة أسابيع.

---

## 🧪 الاختبارات

```bash
python -m pytest tests/ -q
# النتيجة: 248 passed
```

---

## 🧭 خارطة الطريق

| الأولوية | البند                          |
|----------|--------------------------------|
| ⭐⭐     | تشديد مرحلة التعلم تدريجياً   |
| ⭐⭐     | أوامر Telegram تفاعلية         |
| ⭐       | GitHub Pages للداشبورد        |
| ⭐       | دعم Groq في Backtesting        |

---

<div align="center">

**Gold AI Signals — Trade Smart, Test First 🏆**  
*آخر تحديث: 2026-06-25*

</div>