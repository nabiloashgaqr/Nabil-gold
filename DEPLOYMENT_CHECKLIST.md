# ✅ Gold AI Signals - Deployment Checklist

استخدم هذه القائمة بعد رفع التعديلات إلى GitHub لتشغيل النظام بأمان.

## 1) GitHub Secrets المطلوبة

اذهب إلى:

`Repository → Settings → Secrets and variables → Actions → New repository secret`

أضف:

| Secret | مطلوب | ملاحظات |
|---|---:|---|
| `TELEGRAM_BOT_TOKEN` | نعم | توكن بوت Telegram من BotFather |
| `TELEGRAM_CHAT_ID` | نعم | رقم المحادثة/القناة |
| `SUPABASE_URL` | نعم | Project URL من Supabase |
| `SUPABASE_KEY` | نعم | يفضّل Service Role Key وليس anon key |
| `TWELVE_DATA_API_KEY` | نعم | إلزامي لمنع استخدام بيانات وهمية في الإنتاج |
| `OPENAI_API_KEY` | اختياري | إذا كان AI provider = openai |
| `GROQ_API_KEY` | اختياري | بديل رخيص وسريع |
| `ANTHROPIC_API_KEY` | اختياري | بديل |
| `GEMINI_API_KEY` | اختياري | بديل |

> إذا لم تضف `TWELVE_DATA_API_KEY` سيفشل Workflow التحليل عمدًا لحمايتك من إشارات مبنية على بيانات demo.

## 2) Supabase

1. افتح Supabase Dashboard
2. افتح SQL Editor
3. الصق محتوى `supabase_schema.sql`
4. اضغط Run
5. تأكد من وجود الجداول:
   - `trades`
   - `signals`
   - `agent_weights`
   - `learning_history`
   - `agent_evaluations`

## 3) تشغيل الاختبارات

من GitHub:

`Actions → Tests → Run workflow`

يجب أن تنجح الاختبارات.

## 4) فحص الإعدادات داخل GitHub Actions

كل Workflow يحتوي الآن على خطوة:

```bash
python scripts/validate_setup.py <mode>
```

الأوضاع:

```bash
python scripts/validate_setup.py analyze
python scripts/validate_setup.py update-trades
python scripts/validate_setup.py daily-report
```

## 5) تشغيل يدوي أول مرة

شغّل بالترتيب:

1. `Tests`
2. `Daily Report & Learning`
3. `Update Open Trades`
4. `Gold Analysis Bot`

## 6) نقاط أمان مهمة

- لا ترسل أي Personal Access Token في المحادثات أو README.
- استخدم Supabase Service Role Key فقط داخل GitHub Secrets.
- لا تفعل `allow_synthetic_in_production` إلا للاختبار فقط.
- ابدأ Paper Trading لمدة 2-4 أسابيع قبل أي اعتماد فعلي.

## 7) متابعة الصفقات المفتوحة

تم تفعيل:

```json
"trade_management": {
  "update_outside_trading_hours": true
}
```

وهذا يعني أن تحديث الصفقات المفتوحة يمكن أن يستمر حتى خارج ساعات توليد الإشارات.
