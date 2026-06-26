# 📋 تقرير شامل وخطة إصلاح — Nabil-gold

> تاريخ التقرير: 2026-06-26  
> حالة الاختبارات: ✅ 299/299 ناجحة  
> عدد الملفات: ~60 ملف Python + Config + SQL + Workflows

---

## 🏗️ ملخص المشروع

نظام آلي لتوليد إشارات ورقية للذهب وأزواج فوركس ونفط WTI، يعمل عبر GitHub Actions وcron-job.org. القرار النهائي مبني على إجماع موزون من 5 وكلاء تحليل (Technical, Classical, SMC, Price Action, Multi-Timeframe) مع فلاتر أخبار ومخاطر.

### البنية المعمارية
```
Finnhub → 5 Agents → Weighted Consensus → Filters → Telegram → Supabase
```

---

## 🟢 نقاط القوة

| البند | التقييم |
|---|---|
| الاختبارات | ✅ 299 اختبار شامل — نسبة تغطية عالية |
| فصل المسؤوليات | ✅ وكلاء منفصلين + خدمات منفصلة |
| إدارة الأخطاء | ✅ كل وكيل يلتقط الأخطاء ويعود بنتيجة محايدة |
| التخزين | ✅ Supabase + fallback محلي |
| 문서ة README | ✅ مفصلة بالعربية والإنجليزية |
| لا أسرار مكشوفة | ✅ جميع المفاتيح تُقرأ من ENV |
| Multi-Asset | ✅ 8 أدوات مع point_size مخصص |
| Trailing Stop | ✅ مُنجز بشكل احترافي مع early breakeven |

---

## 🔴 المشاكل الحرجة (P0 — يجب إصلاحها فوراً)

### 1. خلأ منطقي في `_consensus_direction` — RiskManagementAgent
**الملف:** `agents/risk_management_agent.py` السطر ~270  
**المشكلة:**
```python
if score > 0 and buy_count >= sell_count:
    direction = "BUY"
elif score < 0 and sell_count >= buy_count:
    direction = "SELL"
elif buy_count > sell_count:
    direction = "BUY"
elif sell_count > buy_count:
    direction = "SELL"
```
**ال bug:** عندما يكون `score > 0` و `sell_count > buy_count`، الشرط الأول يفشل (لأن `buy_count >= sell_count` خاطئ)، والشرط الثالث يفشل أيضاً، في⚱️ يختار SELL رغم أن weighted score موجب! هذا ينتج قرارات عكسية.

**الحل:** إعادة هيكلة المنطق ليعتمد أولاً على الاتجاه حسب score، ثم يكسر التعادل بعدد الوكلاء.

### 2. حجم الصفقة (`_position_size`) لا يدعم Forex/WTI
**الملف:** `agents/risk_management_agent.py` السطر ~560  
**المشكلة:**
```python
# Approximation for XAUUSD: 1 standard lot ~= 100 oz, $1 move ~= $100.
lots = risk_amount / max(price_distance * 100, 0.01)
```
هذه المعادلة **صحيحة فقط للذهب**. للأزواج الفورية (EUR/USD)، الحجم الصحيح يعتمد على `100,000 * price_distance`. للنفط `1000 * price_distance`.

**الحل:** استخدام `point_size` من `utils/instruments.py` لحساب الحجم بشكل صحيح لكل أداة.

### 3. `_rsi_divergence` يمكن أن يسبب IndexError
**الملف:** `agents/technical_agent.py` السطر ~260  
**المشكلة:** عند الوصول إلى `rsi_series[ia]` أو `rsi_series[ib]`، إذا كان الفهرس أكبر من طول السلسلة يحدث crash. المصفوفة `rsi_series` قد تكون أقصر من `candles`.

**الحل:** إضافة حد أقصى للفهرس قبل الوصول.

---

## 🟡 مشاكل متوسطة (P1 — يجب إصلاحها قريباً)

### 4. تكرار دوال مساعدة في كل وكيل
**المشكلة:** `_f()`, `_last()` مكررة في `technical_agent`, `classical_agent`, `smc_agent`, `price_action_agent`, `multitimeframe_agent`, `daily_bias_agent` (6 نسخ!).  
**الحل:** نقلها إلى `BaseAgent`.

### 5. ملف `.gitignore` مكرر
**المشكلة:** يوجد `.gitignore` و `Nabil-gold.gitignore` بنفس المحتوى تقريباً.  
**الحل:** حذف `Nabil-gold.gitignore`.

### 6. أسطر طويلة جداً (>200 حرف)
**العدد:** 51 سطر  
**المشكلة:** تقلل القراءة والصيانة.  
**الحل:** تقسيم الأسطر الطويلة.

### 7. لا يوجد `pyproject.toml` حديث
**المشكلة:** المشروع يستخدم `requirements.txt` فقط بدون إعدادات أدوات (ruff, mypy, pytest).  
**الحل:** إضافة `pyproject.toml` مع إعدادات شاملة.

### 8. لا يوجد `Dockerfile`
**المشكلة:** لا يمكن بناء حاوية موحدة للتشغيل.  
**الحل:** إضافة Dockerfile بسيط.

### 9. `NEWS_EVENTS_JSON` بدون تنظيف كافٍ
**الملف:** `agents/news_risk_agent.py`  
**المشكلة:** `json.loads(env_events)` مباشرة من متغير بيئة قد يكون ضاراً.  
**الحال:** يوجد `sanitize_prompt_text` لكن لا يتم تطبيقه على أسماء الأحداث في جميع الحالات.

### 10. `except Exception:` عامة جداً (18 موضع)
**المشكلة:** تلتقط كل شيء بما فيها `KeyboardInterrupt` (عبر السلسلة).  
**الحل:** تضييق الاستثناءات حيثما أمكن، أو إضافة `except (ValueError, KeyError, TypeError):`.

---

## 🔵 تحسينات وإضافات مطلوبة (P2)

### 11. إضافة `.pre-commit-config.yaml`
لفرض التنسيق والفحص قبل كل commit.

### 12. إضافة `ruff.toml` للتنسيق الموحد
بدلاً من flake8 + black بشكل منفصل.

### 13. إضافة Health Check endpoint
لمراقبة حالة النظام من خارج GitHub Actions.

### 14. تحسين `conftest.py` — مشاركة fixtures
بعض الاختبارات تنشئ mocks متشابهة.

### 15. إضافة `py.typed` marker
لدعم type checking في IDEs.

### 16. تحسين ForexFactory scraper
`services/news_feed_forexfactory.py` يحتاج error handling أفضل.

### 17. إضافة نظام Circuit Breaker لـ Finnhub
عند فشل API بشكل متكرر، إيقاف الطلبات مؤقتاً.

### 18. إضافة `logging.StructuredLogger`
لتنسيق السجلات بشكل JSON في GitHub Actions.

---

## 📊 إحصائيات الكود

| البند | العدد |
|---|---|
| ملفات Python | ~40 |
| أسطر كود (تقريبي) | ~8,500 |
| اختبارات | 299 |
| وكلاء تحليل | 5 |
| وكلاء مساعدين | 5 (decision, risk, daily_bias, news, session) |
| خدمات | 7 |
| سكريبتات | 8 |
| Workflows GitHub | 8 |
| `# noqa` تعليقات | 49 |
| `except Exception:` | 18 |
| أسطر >200 حرف | 51 |

---

## 🛠️ خطة الإصلاح — الخطوات

### المرحلة 1: إصلاحات حرجة (اليوم)
1. ✅ إصلاح `_consensus_direction` في RiskManagementAgent
2. ✅ إصلاح `_position_size` لدعم Forex/WTI
3. ✅ إصلاح `_rsi_divergence` IndexError guard

### المرحلة 2: تحسينات الكود (هذا الأسبوع)
4. ✅ نقل `_f()` و `_last()` إلى BaseAgent
5. ✅ حذف `Nabil-gold.gitignore` المكرر
6. ✅ إضافة `pyproject.toml`
7. ✅ إضافة `Dockerfile`

### المرحلة 3: جودة الكود (الأسبوع القادم)
8. تقسيم الأسطر الطويلة
9. تضييق الاستثناءات
10. إضافة `.pre-commit-config.yaml`
11. إضافة `ruff.toml`

---

**ملاحظة:** جميع الإصلاحات تمر عبر 299 اختبار موجود بدون كسر أي منها.
