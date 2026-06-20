# 🔧 إصلاح حلقة التعلّم المقطوعة

> **التاريخ:** 2026-06-20  
> **الحالة:** ✅ **الحلقة الآن مُغلقة وتعمل**  
> **الاختبارات:** **253/253** نجح (241 + 10 جديد + 2 param)

---

## 📋 المشاكل التي تم اكتشافها

### 🔴 المشكلة 1: Learning Service → Decision Agent (مقطوعة)

**الوصف:**
- `run_learning.py` يحسب أوزان الوكلاء الجديدة **كل يوم** ويحفظها في جدول `agent_weights` على Supabase ✓
- لكن `DecisionAgent._load_weights()` كان يقرأ من `config.json` فقط ✗
- النتيجة: النظام "يتعلم" لكن لا أحد يستمع له

**السبب الجذري:**
```python
# agents/decision_agent.py (قبل)
def _load_weights(self) -> Dict[str, float]:
    config_weights = self.config.get('agent_weights', {})  # ← فقط config.json
    return config_weights.copy() if config_weights else self.default_weights.copy()
```

**الإصلاح:**
```python
# agents/decision_agent.py (بعد)
def _load_weights(self) -> Dict[str, float]:
    # 1) من learning_service (DB) — الأوزان المحدّثة يومياً
    if self.learning_service is not None:
        db_weights = getattr(self.learning_service, 'current_weights', None)
        if db_weights:
            return dict(db_weights)
    # 2) fallback إلى config.json
    # 3) fallback إلى default_weights
```

### 🔴 المشكلة 2: أوزان DB لم تكن تُحمَّل قبل DecisionAgent

**الوصف:**
- حتى لو أصلحنا `_load_weights()`، الـ `LearningService` يبدأ بـ `current_weights = default_weights.copy()` (وليس من DB)
- يجب تحميل الأوزان من DB **قبل** إنشاء DecisionAgent

**الإصلاح في `scripts/run_analysis.py`:**
```python
learning_service = get_learning_service(database, config)
# 🆕 السطر المفقود سابقًا:
await learning_service.load_current_weights()
decision = await DecisionAgent(config, ai_service, learning_service).decide_async(all_results)
```

### 🟡 المشكلة 3: حدّ مراجعة يومي ضعيف

**الوصف:**
- مع زيادة الإشارات اليومية، `max_reviews_per_run: 3` يترك خسائر دون مراجعة
- الصفقات غير المُراجعة تتراكم وتخرج من نافذة `recent_trades_limit: 30`

**الإصلاح في `config.json`:**
```diff
- "max_reviews_per_run": 3,
+ "max_reviews_per_run": 20,
- "recent_trades_limit": 30,
+ "recent_trades_limit": 50,
```

---

## 📦 الملفات في الـ ZIP

```
Nabil-gold-learning-fix.zip
├── agents/
│   └── decision_agent.py                 ← مُعدَّل: _load_weights يستخدم learning_service
├── scripts/
│   └── run_analysis.py                   ← مُعدَّل: يستدعي load_current_weights قبل DecisionAgent
├── tests/
│   └── test_learning_weights_fix.py      ← جديد: 10 اختبارات regression
└── config.json                           ← مُعدَّل: max_reviews_per_run 3→20, recent_trades_limit 30→50
```

---

## 🧪 الاختبارات الـ 10 الجديدة

| الفئة | الاختبار | يَختبر |
|---|---|---|
| `TestLoadWeightsFix` | `test_uses_learning_service_weights_when_available` | يستخدم DB weights بدل config |
| | `test_falls_back_to_config_when_no_learning_service` | fallback إلى config لو لا يوجد service |
| | `test_falls_back_to_config_when_learning_service_has_empty_weights` | fallback لو DB فارغ |
| | `test_falls_back_to_default_when_neither_available` | fallback إلى default_weights |
| | `test_update_weights_replaces_current_weights` | update_weights يحدّث القيم |
| | `test_independent_copy_no_aliasing` | نسخة مستقلة (لا aliasing) |
| | `test_does_not_use_config_when_learning_service_provided_even_with_different_keys` | لا دمج مع config |
| `TestLoadWeightsAsyncIntegration` | `test_learning_service_load_current_weights_signature` | load_current_weights async |
| | `test_run_analysis_imports_load_helpers` | run_analysis يستدعي load_current_weights |
| `TestConfigUpdate` | `test_config_has_updated_review_limits` | max_reviews_per_run ≥ 15 |

---

## 📋 خطوات التطبيق

```bash
cd /path/to/Nabil-gold
unzip -o /path/to/Nabil-gold-learning-fix.zip
python -m pytest tests/test_learning_weights_fix.py -v
python -m pytest tests/  # كل الاختبارات
```

---

## 🎯 كيف تعمل الحلقة الآن (مُغلقة بالكامل)

```
[Daily 20:00 UTC] run_learning.py
    ↓ يحسب أوزان جديدة بناءً على آخر 7 أيام
    ↓ يحفظها في جدول agent_weights على Supabase ✓
[Every 10 min] run_analysis.py
    ↓ ينشئ LearningService جديد
    ↓ 🆕 await learning_service.load_current_weights()  ← يحمل من DB
    ↓ ينشئ DecisionAgent(config, ai_service, learning_service)
    ↓ DecisionAgent._load_weights() ← يستخدم learning_service.current_weights
    ↓ القرار يُحسب بالأوزان المحدّثة ✓
```

---

## ✅ نتائج الاختبار

```
======================= 253 passed, 3 warnings in 1.66s ========================
```

- **241** اختبار أصلي + **10** جديد للتعلم = **251** (العدد الفعلي 253 بسبب parametrize)
- **0** تحذير pyflakes في الكود الإنتاجي
- **0** تحذير pyflakes في الملف الجديد

---

## 💡 التوصيات الإضافية

1. **مراقبة الأوزان في Telegram:** أضف سطر في `run_learning.py` يُرسل الأوزان الجديدة بعد كل تحديث.
2. **سجل التغييرات:** احفظ timestamp + weights القديمة في `learning_history` (موجود بالفعل).
3. **تنبيه عند فشل load_current_weights:** الـ logger.warning موجود لكن يمكن إضافة Telegram alert.
4. **max_reviews_per_run ديناميكي:** حسابه من عدد الخسائر اليومية × معامل (مثلاً 80%).
