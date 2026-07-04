# 🥇 Nabil Gold — نظام إشارات تداول ذكي (Paper Trading)

> بوت تحليلي متعدد الوكلاء لإنشاء إشارات تداور ذهب (XAU/USD) بوضع تجريبي (Paper Trading)، يعتمد على توافق وزني بين 5 وكلاء تحليليين مع فلاتر أمان متعددة الطبقات وإدارة مخاطر ديناميكية.

---

## ⚡ نظرة عامة

- **الرمز المستهدف:** `XAU/USD` (الذهب مقابل الدولار)
- **الأطر الزمنية:** `5m` · `15m` · `1H` · `4H`
- **نوع التنفيذ:** Paper Trading (تجريبي — لا يُنفّذ صفقات حقيقية)
- **منصة التشغيل:** Python 3.12+ مع جداولة عبر GitHub Actions
- **قاعدة البيانات:** Supabase (PostgreSQL) مع fallback محلي JSON
- **الإشعارات:** Telegram Bot (HTML formatting)
- **مزود البيانات:** Twelve Data (Free tier — 800 call/يوم)

---

## 🏗️ بنية النظام

```
Nabil-gold/
├── agents/              # الوكلاء التحليليون (5 + Decision + Risk + News + ...)
├── services/            # خدمات النظام (Telegram, DB, Learning, Backtesting, ...)
├── scripts/             # نقاط الدخول للتشغيل والتقارير
├── dashboard/           # لوحة تحكم ويب (HTML/CSS/JS)
├── api/                 # API Dashboard (Node.js functions)
├── utils/               # أدوات مساعدة ومؤشرات فنية
├── tests/               # 392 اختبار تلقائي (pytest)
├── config.json          # مركزية الإعدادات
└── .github/workflows/   # 12 workflow للجداولة
```

---

## 🤖 الوكلاء التحليليون (5 + 1)

| الوكيل | الدور | الوزن |
|--------|-------|-------|
| 🔬 **Technical** | مؤشرات فنية (RSI, MACD, EMA, ATR, Bollinger) | 20% |
| 📊 **Classical** | نماذج كلاسيكية (دعم/مقاومة، قمم/قيعان، اختراقات) | 25% |
| 🧠 **SMC** | Smart Money Concepts (Order Blocks, Liquidity Sweeps, FVG) | 20% |
| 📈 **Price Action** | Price Action صرف (شموع، تراكم، توزيع) | 20% |
| 🌍 **Multi-Timeframe** | توافق الأطر الزمنية (5m→4H) + حالة الدخول | 15% |
| ⚖️ **Decision Agent** | توافق وزني نهائي + فلاتر الأمان | — |

> **مبدأ التوافق:** يُتطلب موافقة ≥3 وكلاء مؤهلين (ثقة ≥70%) مع صافي ثقة وزني ≥72% بعد خصم المعارضين.

---

## 🛡️ فلاتر الأمان المتعددة الطبقات

- ⏰ **نافذة التداول:** 03:00–22:00 (Asia/Hebron) أيام العمل فقط
- 📰 **فلتر الأخبار:** منع تلقائي قبل/بعد أخبار Tier 1/2 من ForexFactory
- 📉 **Daily Bias (4H):** حظر الصفقات المعاكسة للاتجاه العام إلا بشروط صارمة
- 🚫 **Duplicate Filter:** منع التكرار في نفس منطقة السعر + تبريد واعٍ بالنتيجة
- ⚠️ **Dynamic Risk:** رفع الشروط أو الإيقاف المؤقت بعد خسائر متتالية (معطل حالياً لجمع البيانات)

---

## 📐 إدارة المخاطر والصفقات

| البند | القيمة | الوصف |
|-------|--------|-------|
| SL min distance | 400 pts ($40) | حماية من الضوضاء السعرية |
| Early Breakeven | +200 pts | نقل SL إلى الدخول تلقائياً |
| Trailing Stop | 150 pts gap / 40 pts step | تحريك الستوب تبعاً للربح |
| Partial Close @ TP1 | 50% | تأمين نصف الصفقة عند أول هدف |
| Max Open Trades | 3 | الأصلية + تعزيزان كحد أقصى |
| Scale-In | كل 200 pts | حجم 0.5x للتعزيزات |
| Expire | 24 ساعة | إغلاق تلقائي للصفقات المعلقة |

---

## 🔗 التكاملات

### 📨 Telegram Bot
- إشارات دخول منسقة (HTML) مع تفاصيل الدخول/SL/TP/RR
- أحداث الصفقات (TP1/TP2/SL/Trailing/Breakeven/Fill)
- تقارير يومية (23:00) وأسبوعية (السبت 10:00)
- تنبيهات الأخطاء والحالات الحرجة

### 🗄️ Supabase Database
- تخزين حالة الصفقات المفتوحة بشكل دائم
- جدول `trades` + `trade_snapshots` + `performance_logs`
- schema كامل في `supabase_schema_unified.sql`

### 📡 Twelve Data
- شموع `5m` أساسية مع إعادة التجميع المحلي إلى `15m/1H/4H`
- Spot quotes احتياطية عند نفاد الكوتة

### 🤖 Gemini (اختياري)
- مراجعة مستقلة للإشارة (Independent Review)
- تحليل أخبار ذكي عند توفر `GEMINI_API_KEY`
- لا يُستخدم في قرار التداول النهائي — للملاحظة فقط

---

## 🚀 التشغيل السريع

### 1. المتطلبات
```bash
python -m pip install -r requirements.txt
```

### 2. متغيرات البيئة
انسخ `.env.example` إلى `.env` واملأ:
```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
SUPABASE_URL=...
SUPABASE_KEY=...
TWELVEDATA_API_KEY=...
GEMINI_API_KEY=...        # اختياري
```

### 3. التشغيل المحلي
```bash
python main.py                    # دورة تحليل واحدة
python scripts/run_analysis.py    # نفس الوظيفة
python scripts/run_trade_updates.py   # تحديث الصفقات المفتوحة
python scripts/run_daily_report.py    # التقرير اليومي
python scripts/run_weekly_report.py   # التقرير الأسبوعي
```

### 4. Docker
```bash
docker build -t nabil-gold .
docker run --env-file .env nabil-gold
```

---

## 🧪 الاختبارات

```bash
pytest tests/ -q
```

- **392 اختبار** يغطون الوكلاء، الفلاتر، إدارة الصفقات، التنسيق، والتكامل
- جميع الاختبارات يجب أن تنجح قبل أي تعديل إنتاجي

---

## ⚙️ GitHub Actions Workflows

| Workflow | الجدولة | الوظيفة |
|----------|---------|---------|
| `analyze.yml` | كل 5 دقائق | تحليل السوق وإرسال الإشارات |
| `update_trades.yml` | كل 5 دقائق | متابعة الصفقات المفتوحة (SL/TP/Trailing) |
| `daily_report.yml` | 23:00 يومياً | التقرير اليومي |
| `weekly_report.yml` | السبت 10:00 | التقرير الأسبوعي |
| `macro_context.yml` | كل ساعة | تحديث البيانات الكلية |
| `dashboard.yml` | — | توليد لوحة التحكم |
| `tests.yml` | عند Push | تشغيل الاختبارات |
| `backtest.yml` | يدوي | باكتست تاريخي |
| `telegram_test.yml` | يدوي | اختبار إرسال Telegram |

---

## 📁 ملفات الإعداد الرئيسية

| الملف | الغرض |
|-------|-------|
| `config.json` | جميع الإعدادات المركزية (أوزان، مخاطر، جداول، فلاتر) |
| `utils/helpers.py` | مصدر واحد للحقائق المشتركة (weights, sessions, formatting) |
| `supabase_schema_unified.sql` | هيكل قاعدة البيانات الكامل |

---

## 🎯 نقاط القوة

- ✅ لا يعتمد على نماذج لغوية خارجية في قرار التداول — قرار صرف من الوكلاء التحليليين
- ✅ فلاتر أمان متعددة الطبقات تقلل الإشارات الضعيفة والمتهورة
- ✅ إدارة صفقات ذكية (Breakeven + Trailing + Partial Close)
- ✅ نظام تعلم ذكي لتحليل أداء الوكلاء وتقديم توصيات أوزان
- ✅ دعم ثنائي اللغة (عربي/إنجليزي) في التوثيق والتعليقات
- ✅ 392 اختبار تلقائي لضمان استقرار كل إصدار

---

## ⚠️ تنويه

> هذا النظام يعمل في **وضع تجريبي (Paper Trading)** فقط. الإشارات التي يُرسلها هي لأغراض التقييم والتجربة، ولا تشكل توصية مالية أو استثمارية. استخدمها على مسؤوليتك الشخصية.

---

**المطور:** Nabil Ashqar  
**الموقع:** Nablus, Palestine 🇵🇸  
**التوقيت:** Asia/Jerusalem
