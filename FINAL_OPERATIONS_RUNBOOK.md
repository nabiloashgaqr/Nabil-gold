# SmartSignal — Final Operations Runbook

## الهدف
هذا الملف يوضح الترتيب النهائي الموصى به لتشغيل النظام بعد اكتمال التطويرات الأساسية.

## المسارات الرئيسية
### 1) التشغيل اليومي العادي
- `python scripts/run_analysis.py`
- `python scripts/run_trade_updates.py`
- `python scripts/run_daily_report.py`
- `python scripts/run_weekly_report.py`

### 2) القياس والتقييم
#### باك تست عادي
```bash
python scripts/run_backtest.py --timeframe 15m --outputsize 420 --window 160 --step 12 --horizon 32 --max-trades 60
```

#### Benchmark مقارنة current vs baseline
```bash
python scripts/run_backtest.py --benchmark --timeframe 15m --outputsize 420 --window 160 --step 12 --horizon 32 --max-trades 60
```

### 3) Analyst Distillation
#### استيراد labels
```bash
python scripts/import_analyst_labels.py path/to/labels.json
```
أو:
```bash
python scripts/import_analyst_labels.py path/to/labels.csv
```

#### تشغيل المقارنة
```bash
python scripts/run_analyst_comparison.py
```

### 4) Final Evaluation Pass
```bash
python scripts/run_final_evaluation.py --timeframe 15m --outputsize 420 --window 160 --step 12 --horizon 32 --max-trades 60
```

الناتج الافتراضي:
- `storage/final_evaluation.json`

### 5) Tuning Advisor
```bash
python scripts/run_tuning_advisor.py --input storage/final_evaluation.json --output storage/tuning_advice.json
```

الناتج الافتراضي:
- `storage/tuning_advice.json`

### 6) Release Readiness
```bash
python scripts/run_release_readiness.py --timeframe 15m --outputsize 420 --window 160 --step 12 --horizon 32 --max-trades 60
```

الناتج الافتراضي:
- `storage/release_readiness.json`

### 7) الحزمة التشغيلية الكاملة (الموصى بها)
```bash
python scripts/run_operations_pipeline.py --timeframe 15m --outputsize 420 --window 160 --step 12 --horizon 32 --max-trades 60
```

الناتج الافتراضي في:
- `storage/ops_pipeline/final_evaluation.json`
- `storage/ops_pipeline/tuning_advice.json`
- `storage/ops_pipeline/release_readiness.json`

## كيف تتخذ القرار بعد التشغيل؟
### إذا كان القرار النهائي:
- `PROCEED_TO_STRUCTURED_TRIAL`
  - ابدأ تجربة منظمة forward trial
  - لا تغيّر config مباشرة إلا إذا كانت التعديلات المقترحة صغيرة وواضحة

- `APPLY_TUNING_THEN_REEVALUATE`
  - طبّق patch محافظ من `tuning_advice.json`
  - أعد تشغيل:
    - final evaluation
    - release readiness

- `HOLD_AND_REFINEMENT_REQUIRED`
  - لا تبدأ structured trial
  - راجع:
    - benchmark deltas
    - top missed reasons
    - not-filled ratio
    - overlap quality

## الترتيب العملي الموصى به أسبوعيًا
1. استيراد analyst labels الجديدة
2. تشغيل `run_analyst_comparison.py`
3. تشغيل `run_backtest.py --benchmark`
4. تشغيل `run_final_evaluation.py`
5. تشغيل `run_tuning_advisor.py`
6. تشغيل `run_release_readiness.py`

أو ببساطة:
```bash
python scripts/run_operations_pipeline.py
```

## ملاحظات تشغيلية
- استخدم نفس `config.json` الحالي كمصدر وحيد للحقائق.
- لا تطبق patches على config تلقائيًا مباشرة في الإنتاج بدون مراجعة.
- analyst overlap بدون labels كافية لا يكفي وحده لاتخاذ قرار.
- benchmark وحده لا يكفي أيضًا بدون فهم overlap وnot-filled ratio.
- أفضل قراءة نهائية تأتي من دمج:
  - benchmark
  - overlap
  - tuning advice
  - release readiness

## الملفات المرجعية الأساسية
- `services/backtesting.py`
- `services/final_evaluation.py`
- `services/tuning_advisor.py`
- `services/release_readiness.py`
- `services/analyst_distillation.py`
- `scripts/run_backtest.py`
- `scripts/run_final_evaluation.py`
- `scripts/run_tuning_advisor.py`
- `scripts/run_release_readiness.py`
- `scripts/run_operations_pipeline.py`
