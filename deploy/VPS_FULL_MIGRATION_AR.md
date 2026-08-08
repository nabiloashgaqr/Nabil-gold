# 🖥️ النقل الكامل إلى السيرفر — GitHub يتوقف إلا عن اللوحة (Dashboard)

## القرار المعتمد
- **السيرفر (Contabo Windows Server 2022)** يشغّل **كل شيء**: التحليل، تحديث الصفقات، الـ tick manager، الـ demo، وبوت الاشتراكات.
- **GitHub** يبقى له فقط: مستودع الكود (نسخ احتياطي + git pull) + **الـ Dashboard** + اختبار CI عند الـ push.

## مبدأ التصميم: نسخة واحدة من الكود، تياران
نسخة واحدة من الفرع `demo/mt5` على السيرفر في `C:\Nabil-gold` تشغّل تيارين بمتغيرات بيئة مختلفة:

| التيار | EXECUTION_MODE | TRADES_TABLE | مصدر البيانات | القناة |
|---|---|---|---|---|
| 📄 Paper (الحقيقة الأساسية) | paper | trades | TwelveData | الرئيسية |
| 🧪 Demo (ظل MT5) | mt5_demo | trades_demo | MT5 | الديمو |

> بهذا تكون مقارنة الـ shadow عادلة: نفس القواعد الموحّدة الجديدة في التيارين،
> والفرق الوحيد هو طبقة التنفيذ (MT5 الحقيقي مقابل المحاكاة).

## المهام المجدولة (Task Scheduler)

| المهمة | التكرار | السكريبت | الدور |
|---|---|---|---|
| SS_PaperAnalysis | كل 5 دقائق | run_analysis.py | تحليل الورقي + الإشارات |
| SS_PaperUpdates | كل دقيقة | run_trade_updates.py | إدارة صفقات الورقي (BE/تريلينج/خروج) |
| SS_MacroContext | كل ساعة | update_macro_context.py | سياق الماكرو |
| SS_DailyReport | يومياً 23:00 UTC | سلسلة التقرير اليومي | updates(force) → learning → report |
| SS_WeeklyReport | سبت 07:00 UTC | run_weekly_report.py | ≈10:00 بتوقيت الخليل صيفاً |
| SS_SubscriptionBot | يومياً 00:00 | subscription_bot/cron_maintenance.py | الاشتراكات |
| SS_DemoAnalysis | كل 5 دقائق | run_analysis.py (demo env) | إشارات الديمو |
| SS_DemoWatchdog | كل دقيقة | demo_watchdog.py | مراقبة نبض الـ demo loop |
| SS_DemoLoop | عند الدخول (دائم) | run_demo_loop.py | تدقيق/مطابقة كل 5 دقائق |
| SS_TickManager | عند الدخول (دائم) | run_tick_manager.py | **التنفيذ على كل تكة** |
| SS_MT5Terminal | عند الدخول | terminal64.exe | طرفية MT5 تعمل دائماً |

**حماية من التكرار:** الـ tick manager والـ demo loop عمليات دائمة — أضفنا حارس
`utils/single_instance.py` (ملف pid): أي نسخة ثانية تكتشف النسخة الحية وتخرج فوراً،
فلا إدارة مزدوجة لأوامر MT5 أبداً. (مُختبَر في tests/test_vps_task_guards.py)

## كيف تُحقن متغيرات البيئة؟
- كل مهمة لها ملف `.bat` في `deploy/tasks/` يحدد EXECUTION_MODE / TRADES_TABLE /
  DATA_SOURCE_PRIMARY الخاصة بتيارها ثم يشغّل السكريبت ويكتب اللوج في `logs\`.
- ملف `.env` يحمل **الأسرار فقط** (Supabase / Telegram / LLM / MT5)، وقيمته الافتراضية
  للتيار هي الورقي (الأأمن). متغيرات الـ bat الحقيقية تتغلب دائماً على `.env`.
- أضفنا `load_dotenv()` في أول كل سكريبت مجدول — على GitHub Actions لا يتغير شيء
  (لا يوجد .env هناك)، وعلى السيرفر يقرأ `.env` تلقائياً.

## ترتيب التشغيل الصحيح (مهم!)
1. استئجار السيرفر وتجهيزه: `deploy\vps_setup.ps1` (كمسؤول).
2. تعبئة `.env` + تسجيل دخول MT5 الديمو يدوياً مرة + تفعيل الدخول التلقائي (netplwiz).
3. اختبار يدوي: `deploy\tasks\demo_smoke.bat` ثم تشغيل `paper_analysis.bat` يدوياً مرة.
4. **أثناء الاختبار أوقف مهام الجدولة أو احذفها مؤقتاً حتى لا يتزامن السيرفر مع GitHub.**
5. **القطع**: اتبع `deploy\GITHUB_SHUTDOWN_AR.md` — أوقف مشغّلات GitHub أولاً، ثم فعّل مهام السيرفر.

## تنبيهات
- ⚠️ **لا تشغّل GitHub Actions والسيرفر معاً على نفس الجدول** — يعني صفقات مكررة وبطاقات مزدوجة في نفس جدول trades.
- تكلفة LLM تتضاعف مؤقتاً (تحليلان كل 5 دقائق: ورقي + ديمو) — هذا تصميم الـ shadow، ويمكن لاحقاً تخفيف إيقاع الديمو.
- تحديث الكود: الجولات من هنا → ارفعها على GitHub → على السيرفر `git pull origin demo/mt5`.
- المنطقة الزمنية للسيرفر = UTC (مثل runners القديمة) حتى لا تتزحزح مواعيد التقارير.
- اللوجات: `C:\Nabil-gold\logs\*.log` لكل مهمة على حدة.
