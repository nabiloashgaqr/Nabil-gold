# 🔄 حزمة المزامنة: الفرع = السيرفر = نسختنا المعتمدة (1700 اختبار أخضر)

فحصتُ الفرع الحي سطراً بسطر. الفرع متأخر عن السيرفر/نسخة العمل. هذه الحزمة
تحتوي **كل** الملفات المعدلة بمساراتها — فكّها فوق الفرع وفوق السيرفر فيصبح
الثلاثة مطابقين.

## 1) ارفع للفرع demo/mt5 (استبدال/إضافة)
- config.json  (24/5 + عطلة + الشموع الأربع + إطفاء daily bias)
- utils/helpers.py  (بوابة العطلة is_weekend_hebron)
- scripts/run_analysis.py · run_trade_updates.py · run_demo_loop.py
- deploy/vps_setup.ps1  (ASCII + المهام التسع)
- deploy/.env.example · START_HERE.bat · QUICKSTART_VPS_AR.md · README.md
- deploy/tasks/ العشرة: demo_analysis · demo_loop · demo_watchdog · tick_manager ·
  demo_smoke · subscription_bot · market_status · macro_demo ·
  daily_report_demo · weekly_report_demo
- tests/: test_operator_directives_24h_15m_weekend.py (جديد) +
  test_config_trading_window.py + test_validate_setup_quota.py (محدّثان)
- .github/workflows/analyze.yml (الحاجز)

## 2) احذف من الفرع (مخلفات)
- .env.example (الجذر)
- subscription_bot/.env.example
- PHASE6_ANALYST_DISTILLATION_MIGRATION.sql
- deploy/tasks/daily_report.bat · macro_context.bat · weekly_report.bat
  (نسخ الورقي القديمة — البديل *_demo.bat موجود أعلاه)

## 3) مزامنة السيرفر C:\Nabil-gold
1. فكّ نفس الحزمة فوق C:\Nabil-gold (استبدال).
2. احذف من السيرفر نفس المخلفات الستة (اختياري للنظافة).
3. سجّل مهام التقارير الأربع إن لم تكن مسجلة (أو أعد تشغيل vps_setup.ps1 —
   آمن ويسجل الكل):
```
$t = "C:\Nabil-gold\deploy\tasks"
schtasks /Create /TN "SS_MarketStatus" /SC HOURLY /MO 1 /TR "cmd /c $t\market_status.bat" /F
schtasks /Create /TN "SS_MacroContext" /SC HOURLY /MO 1 /TR "cmd /c $t\macro_demo.bat" /F
schtasks /Create /TN "SS_DailyReport" /SC DAILY /ST 23:00 /TR "cmd /c $t\daily_report_demo.bat" /F
schtasks /Create /TN "SS_WeeklyReport" /SC WEEKLY /D SAT /ST 07:00 /TR "cmd /c $t\weekly_report_demo.bat" /F
```
4. لا ريستارت مطلوب: كل دورة قادمة تقرأ الإعداد الجديد تلقائياً.
   (المهام الدائمة tick/loop تبقى تعمل — لا تلمسها.)

## تحقق أخير بعد الرفع
على السيرفر: Select-String "is_weekend" C:\Nabil-gold\utils\helpers.py
وفي config: Select-String "Full-Day" C:\Nabil-gold\config.json
إن ظهرتا = السيرفر والفرع والنسخة المعتمدة ثلاثةٌ متطابقة.
