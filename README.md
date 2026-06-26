# Multi-Asset AI-Free Signals — Paper-Trading Bot

نظام آلي لتوليد إشارات ورقية للذهب، 6 أزواج فوركس رئيسية، ونفط WTI، وإدارتها عبر Telegram وSupabase. القرار النهائي مبني على **إجماع موزون من 5 وكلاء تحليل** مع فلاتر أخبار ومخاطر، بدون أي API قرار خارجي.

> ⚠️ المشروع تعليمي/تجريبي فقط. لا يُعد توصية مالية ولا ينفذ أوامر حقيقية.

---

## الحالة الحالية

| البند | القيمة |
|---|---|
| وضع التداول | Paper Trading |
| القرار النهائي | 5-Agent Weighted Consensus |
| مصدر البيانات | Twelve Data |
| التخزين | Supabase |
| الإشعارات | Telegram |
| تشغيل التحليل | cron-job.org → GitHub Actions |
| الإشارات | كل 5 دقائق، الإثنين–الجمعة، 24 ساعة |
| تحديث الصفقات | كل 5 دقائق، فقط عند وجود صفقة/أمر نشط |
| حالة السوق | كل ساعة عبر cron-job.org |
| التقرير اليومي | 23:00 بتوقيتك المحلي |
| التقرير الأسبوعي | السبت 10:00 بتوقيتك المحلي |
| آخر فحص | 299 اختبار ناجح |

---

## الأدوات المدعومة وحساب النقاط

يدعم النظام الآن 8 أدوات:

| الرمز | النوع | حجم النقطة | مثال 100 نقطة | خانات العرض |
|---|---|---:|---:|---:|
| XAU/USD | ذهب | 0.10 | 10.00 دولار | 2 |
| EUR/USD | فوركس | 0.00001 | 10 pips | 5 |
| GBP/USD | فوركس | 0.00001 | 10 pips | 5 |
| USD/JPY | فوركس | 0.001 | 10 pips | 3 |
| USD/CHF | فوركس | 0.00001 | 10 pips | 5 |
| USD/CAD | فوركس | 0.00001 | 10 pips | 5 |
| AUD/USD | فوركس | 0.00001 | 10 pips | 5 |
| WTI/USD | نفط WTI | 0.01 | 1.00 دولار | 2 |

كل حسابات PnL وSL وTP وTrailing تستخدم `point_size` الخاص بالرمز، لذلك تختلف النقاط بين الذهب والفوركس والنفط بشكل صحيح.

---

## كيف يعمل النظام

```text
Twelve Data
  → 5 Analysis Agents
  → Weighted Consensus
  → News / Session / Risk / Duplicate Filters
  → Telegram Signal
  → Supabase Trade Record
  → Trade Updates / SL / TP / Trailing
```

### وكلاء التحليل الخمسة

| الوكيل | الدور |
|---|---|
| Technical | مؤشرات فنية، RSI/EMA/MACD/ATR ومستويات |
| Classical | دعم/مقاومة، شموع وأنماط كلاسيكية |
| SMC | Order Blocks, Liquidity, FVG, Structure |
| Price Action | سلوك السعر والشموع والرفض |
| Multi-Timeframe | توافق 5m/15m/1H/4H |

---

## منطق القرار النهائي

### 1. فلترة الوكلاء

أي وكيل ثقته أقل من:

```text
60%
```

لا يُحسب في القرار.

### 2. الدخول العادي

الدخول العادي يحتاج:

```text
2 وكلاء مؤهلين على الأقل بنفس الاتجاه
والثقة الموزونة الصافية ≥65%
```

لا يوجد دخول من وكيل واحد، حتى لو كانت ثقته عالية.

### 3. خصم المعارضة

الوكلاء الموافقون يضيفون وزنهم، والوكلاء المعارضون يخصمون من قوة الإشارة.

مثال:

```text
Technical BUY 66% وزن 0.20
SMC BUY 66% وزن 0.25
Price Action SELL 80% وزن 0.15
```

الحساب:

```text
BUY score = (66/100×0.20) + (66/100×0.25) = 0.297
SELL opposition = (80/100×0.15) = 0.120
BUY edge = 0.297 - 0.120 = 0.177
```

تُخصم المعارضة من الثقة النهائية. إذا نزلت الثقة الصافية تحت 65% فالقرار يصبح WAIT.

### 4. عكس الاتجاه الأعلى Daily Bias

إذا كانت الصفقة عكس اتجاه 4H/Daily Bias:

```text
2 وكلاء مؤهلين على الأقل
والثقة الصافية بعد الخصم ≥75%
```

---

## إدارة المخاطر والصفقة

### قبل الدخول

`RiskManagementAgent` يحسب:

- Entry
- Stop Loss
- TP1 / TP2 / TP3
- R:R
- Position size تقديري
- Trade grade

### أثناء الصفقة

| الحدث | التصرف |
|---|---|
| ربح +100 نقطة | نقل SL إلى الدخول |
| بعد نقل SL | يبدأ trailing مباشرة |
| كل +30 نقطة إضافية | تحريك SL بمقدار 30 نقطة |
| Trailing gap | 100 نقطة خلف السعر |
| TP1 | حماية/جزئي حسب الخطة |
| TP2 | إغلاق رابح |
| SL | إغلاق خسارة أو ربح مقفول إذا كان trailing SL |
| Expiry | بعد 24 ساعة إلا إذا الصفقة رابحة ومحمية |

رسالة الإشارة تعرض سطر الإدارة:

```text
Management: SL → entry after +100 pts · Trail gap 100 pts / step 30 pts · check 5m
```

---

## رسائل Telegram

### الإشارات

تصل فقط عند وجود صفقة مؤهلة. تحتوي على:

- BUY/SELL
- السعر والثقة والجودة
- خطة الدخول وSL/TP
- إدارة الصفقة
- أصوات الوكلاء
- سبب الدخول
- المخاطر
- رقم الصفقة

### تحديث الصفقات

تصل فقط عند حدوث تغيير فعلي:

- Order filled
- SL moved to entry
- Trailing stop moved
- TP1 / TP2
- SL / Trailing SL
- Break-even
- Expired / Manual close

لا تُرسل رسائل لمجرد NEAR_TP1 أو LONG_RUNNING أو EXIT_WARNING.

### حالة السوق

تُرسل من Cron Job خارجي مخصص كل ساعة فقط، وليس من تشغيلات التحليل كل 5 دقائق.

---

## جدولة cron-job.org

### 1. تحليل الإشارات — كل 5 دقائق أيام العمل

```cron
*/5 * * * 1-5
```

POST:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/analyze.yml/dispatches
```

Body:

```json
{
  "ref": "main",
  "inputs": {
    "send_status": "false"
  }
}
```

### 2. تحديث الصفقات — كل 5 دقائق أيام العمل

```cron
*/5 * * * 1-5
```

POST:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/update_trades.yml/dispatches
```

Body:

```json
{
  "ref": "main"
}
```

> يبدأ Workflow التحديث بفحص Supabase. إذا لا توجد صفقة OPEN/PARTIAL/TP1_HIT/PENDING يتوقف مبكراً قبل checkout/pip/جلب السعر.

### 3. حالة السوق — كل ساعة أيام العمل

```cron
2 * * * 1-5
```

POST إلى نفس Workflow التحليل:

```text
https://api.github.com/repos/nabiloashgaqr/Nabil-gold/actions/workflows/analyze.yml/dispatches
```

Body:

```json
{
  "ref": "main",
  "inputs": {
    "send_status": "true"
  }
}
```

---

## GitHub Actions

| Workflow | الملف | ملاحظات |
|---|---|---|
| Analysis | `.github/workflows/analyze.yml` | بدون schedule داخلي، يعمل من cron-job.org |
| Update Trades | `.github/workflows/update_trades.yml` | بدون schedule داخلي، يعمل من cron-job.org |
| Daily Report | `.github/workflows/daily_report.yml` | 23:00 محلياً |
| Weekly Report | `.github/workflows/weekly_report.yml` | السبت 10:00 محلياً |
| Dashboard | `.github/workflows/dashboard.yml` | Artifact + Telegram summary |
| Tests | `.github/workflows/tests.yml` | فحص الكود |

---

## Secrets المطلوبة

أضفها من:

```text
Repository → Settings → Secrets and variables → Actions
```

| Secret | الاستخدام |
|---|---|
| TELEGRAM_BOT_TOKEN | إرسال رسائل Telegram |
| TELEGRAM_CHAT_ID | القناة/المجموعة |
| SUPABASE_URL | قاعدة البيانات |
| SUPABASE_KEY | مفتاح Supabase |
| TWELVE_DATA_API_KEY | بيانات XAU/USD |

لا حاجة لأي مفاتيح قرار خارجي.

---

## Supabase

الملف:

```text
supabase_schema_unified.sql
```

الجداول الأساسية:

| جدول | وظيفة |
|---|---|
| trades | الصفقات وحالاتها |
| signals | أرشيف الإشارات |
| agent_weights | أوزان الوكلاء |
| learning_history | تاريخ التعلم |
| daily_reports | تقارير يومية |
| weekly_reports | تقارير أسبوعية |
| portfolio | ملخص المحفظة الورقية |

---

## التشغيل المحلي

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

تحليل واحد:

```bash
python scripts/run_analysis.py
```

تحديث الصفقات:

```bash
python scripts/run_trade_updates.py
```

تقرير يومي:

```bash
python scripts/run_daily_report.py
```

---

## هيكل المشروع

```text
Nabil-gold/
├── .github/workflows/
├── agents/
├── services/
├── scripts/
├── tests/
├── config.json
├── requirements.txt
├── supabase_schema_unified.sql
└── README.md
```

---

## ملاحظات تشغيلية

- المستودع Public، لذلك GitHub Actions standard runners مجانية للمستودعات العامة حسب سياسة GitHub.
- لا توجد رسائل Telegram من التحليل كل 5 دقائق إلا عند وجود صفقة أو خطأ.
- حالة السوق لها Cron Job منفصل كل ساعة.
- تحديث الصفقات لا يعمل تشغيل ثقيل إلا عند وجود صفقة نشطة أو معلقة.

---

## خارطة الطريق

| أولوية | بند |
|---|---|
| عالية | مراقبة أداء قاعدة 2 وكلاء / 65% لمدة أسبوع |
| عالية | تشديد/تخفيف Daily Bias حسب النتائج |
| متوسطة | تحسين Dashboard |
| متوسطة | إضافة أوامر Telegram مثل `/status` و`/open` |
| منخفضة | تحسين backtesting للإجماع الموزون |

---

**Gold AI Signals — Paper first, measure everything.**
