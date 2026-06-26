# 🔧 إصلاح خطأ "synthetic_demo data detected"

## المشكلة
```
Analysis blocked: synthetic_demo data detected in production timeframes: 15m, 1H, 4H, 5m.
Configure FINNHUB_API_KEY.
```

## السبب
مفتاح Finnhub API غير موجود أو غير صالح في GitHub Secrets.

## الحل

### الخطوة 1: احصل على مفتاح Finnhub المجاني
1. اذهب إلى: https://finnhub.io/register
2. سجّل حساب جديد (مجاني)
3. انسخ الـ API Key من: https://finnhub.io/dashboard

### الخطوة 2: أضف المفتاح في GitHub
1. اذهب إلى المستودع: https://github.com/nabiloashgaqr/Nabil-gold
2. Settings → Secrets and variables → Actions
3. اضغط "New repository secret"
4. الاسم: `FINNHUB_API_KEY`
5. القيمة: المفتاح الذي حصلت عليه (مثال: `d1f2g3h4i5j6k7l8m9n0`)
6. اضغط "Add secret"

### الخطوة 3: تأكد من وجود جميع Secrets المطلوبة
تأكد أن لديك هذه الـ Secrets كلها:

| Secret | مطلوب لـ |
|---|---|
| `TELEGRAM_BOT_TOKEN` | جميع الـ Workflows |
| `TELEGRAM_CHAT_ID` | جميع الـ Workflows |
| `SUPABASE_URL` | التحليل + التحديث + التقارير |
| `SUPABASE_KEY` | التحليل + التحديث + التقارير |
| `FINNHUB_API_KEY` | التحليل + التحديث + الـ Backtest |

### الخطوة 4: شغّل Workflow مرة أخرى
1. اذهب إلى Actions tab
2. اختر "📊 Gold Analysis Bot"
3. اضغط "Run workflow"

---

## تشخيص متقدم
إذا استمرت المشكلة بعد إضافة المفتاح، شغّل هذا السكريبت محلياً:

```bash
export FINNHUB_API_KEY="your_key_here"
python scripts/validate_setup.py analyze
```

ثم اختبر الاتصال مباشرة:
```bash
python -c "
import requests
key = 'YOUR_KEY_HERE'
r = requests.get('https://finnhub.io/api/v1/forex/candle', params={
    'symbol': 'OANDA:XAU_USD',
    'resolution': '15',
    'from': 1719000000,
    'to': 1719100000,
    'token': key
})
print(f'Status: {r.status_code}')
print(f'Response: {r.json()}')
"
```

إذا كان الرد `s: "no_data"` فالمفتاح صالح لكن لا توجد بيانات للنطاق الزمني.
إذا كان الرد `error` فالمفتاح غير صالح.
