# دليل استيراد Analyst Labels

## الملفات الجاهزة
- `analyst_labels_template.json`
- `analyst_labels_template.csv`

## الحقول المهمة
- `id`: معرف فريد لكل label
- `symbol`: مثل `XAU/USD`
- `timeframe`: مثل `15m`
- `analyst_name`: اسم المحلل
- `bias`: `BUY` أو `SELL`
- `setup_type`: مثل `LIQUIDITY_REVERSAL` أو `TREND_CONTINUATION`
- `sweep_side`: `buy_side` أو `sell_side`
- `poi_type`: مثل `order_block` أو `fvg`
- `poi_quality_grade`: مثل `A` أو `B`
- `intended_entry`: سعر الدخول الذي يراه المحلل
- `invalidation`: شرط الإلغاء
- `tp1`, `tp2`: الأهداف
- `session_label`: مثل `London / Europe Midday`
- `trade_decision`: عادة `TRADE`
- `created_at`: وقت الـ label بصيغة ISO

## الاستيراد محلياً
### JSON
```bash
python scripts/import_analyst_labels.py analyst_labels_template.json
```

### CSV
```bash
python scripts/import_analyst_labels.py analyst_labels_template.csv
```

## الاستيراد عبر GitHub workflow اليدوي
من Actions -> Manual SmartSignal Operator:
- اختر العملية: `analyst_comparison`

> ملاحظة: workflow الحالي يشغّل المقارنة، وليس الاستيراد من ملف مرفوع داخل GitHub مباشرة. إذا أردت الاستيراد على GitHub، ارفع الملف أولاً إلى المستودع أو شغّل السكربت محلياً/على السيرفر ثم ادفع البيانات إلى Supabase.

## الفحص بعد الاستيراد
نفّذ في Supabase:
```sql
select count(*) from analyst_labels;
```

ثم:
```sql
select id, symbol, bias, setup_type, created_at
from analyst_labels
order by created_at desc
limit 20;
```
