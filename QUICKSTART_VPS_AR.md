# جاهز للنسخ — دليل التشغيل السريع على VPS ويندوز (ديمو فقط)

الحالة النهائية: **السيرفر يشغّل الديمو فقط**. الورقي متوقف. GitHub للوحة فقط.

انسخ الفرع `demo/mt5` كاملاً إلى `C:\Nabil-gold` ثم:

1. ثبّت MetaTrader 5 وسجّل دخول حساب الديمو مرة واحدة (المهمة SS_MT5Terminal
   تقلعه مع الويندوز).
2. عبّئ `.env` من `deploy\.env.example`:
   - Supabase + TELEGRAM_BOT_TOKEN + **TELEGRAM_CHAT_ID (قناة المشتركين)**.
   - GEMINI_API_KEY.
   - MT5_LOGIN / MT5_PASSWORD / MT5_SERVER.
   - **اترك TELEGRAM_DEMO_CHAT_ID فارغاً** → الإشارات تصل لقناة المشتركين
     بنفس أسلوب الورقي تماماً (بدون بادئة 🧪).
   - لا حاجة لأي مفتاح TwelveData.
3. تأكد أن `deploy\trades_demo.sql` نُفذ مرة في Supabase SQL Editor (جدول الديمو).
4. انقر START_HERE.bat (كمسؤول): يثبّت Git/Python/الحزم، يجدول المهام الخمس
   (SS_DemoAnalysis · SS_TickManager · SS_DemoLoop · SS_DemoWatchdog · SS_MT5Terminal)،
   ويشغّل اختبار الدخان.
5. ظهور «SMOKE OK» = جاهز.
6. فعّل الدخول التلقائي للويندوز (netplwiz) حتى تعود المهام الدائمة بعد إعادة التشغيل.
7. اقطع GitHub حسب `deploy\GITHUB_SHUTDOWN_AR.md` (عطّل Demo Cycle أولاً، ثم مهام
   cron-job.org الورقية).

## ماذا يحدث بعدها
- كل 5 دقائق: تحليل كامل من بيانات MT5 → الإشارة تُحفظ في `trades_demo` وتُرسل لقناة المشتركين.
- على كل تكة: الـ tick manager يدير التفعيل/BE/TP1/التريلينج عبر MT5.
- اللوحة في GitHub تعرض أداء الديمو حصراً.
- لو انقطع فيد MT5: الدورة تتوقف نظيفاً — لا بيانات صناعية ولا إشارات خاطئة أبداً.

لا حساب حقيقي في أي مرحلة.
