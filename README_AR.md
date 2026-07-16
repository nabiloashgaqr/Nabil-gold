# حزمة تعديلات الصفقات المعلقة

هذه الحزمة تجمع الملفات التي عدّلناها والمتعلقة مباشرة بميزة **الصفقات المعلقة Pending Orders**، وتشمل:

## 1) منطق التنفيذ والمتابعة
- `agents/decision_agent.py`
- `agents/open_trades_manager.py`
- `agents/risk_management_agent.py`
- `agents/smc_agent.py`
- `config.json`
- `services/database.py`
- `services/telegram_bot.py`
- `services/pending_governor.py`
- `services/backtesting.py`
- `services/performance_dashboard.py`
- `services/final_evaluation.py`
- `services/release_readiness.py`

## 2) السكربتات التشغيلية
- `scripts/run_analysis.py`
- `scripts/run_backtest.py`
- `scripts/run_trade_updates.py`
- `scripts/run_daily_report.py`
- `scripts/run_final_evaluation.py`
- `scripts/run_release_readiness.py`

## 3) الـ Dashboard / API
- `api/dashboard.js`
- `dashboard/api/dashboard.js`
- `dashboard/app.js`
- `dashboard/index.html`

## 4) الاختبارات
- `tests/test_signal_formatting.py`
- `tests/test_market_status_news_block.py`
- `tests/test_hourly_status.py`
- `tests/test_open_trades_manager.py`
- `tests/test_pending_order_replacement.py`
- `tests/test_pending_governor.py`
- `tests/test_poi_selection_primary_standby.py`
- `tests/test_backtest_benchmark.py`
- `tests/test_final_evaluation.py`
- `tests/test_release_readiness.py`
- `tests/test_release_readiness_no_labels.py`
- `tests/test_release_readiness_structured_trial.py`
- `tests/test_setup_state_machine.py`
- `tests/test_sprint1_setup_candidates.py`

## 5) ملفات SQL الخاصة بـ Supabase
- `SPRINT1_SUPABASE_MIGRATION.sql`
- `SPRINT2_SETUP_STATE_MIGRATION.sql`
- `SUPABASE_VERIFICATION_CHECKLIST.sql`
- `supabase_schema_unified.sql`

## ملاحظات مهمة
- ملفات SQL الموجودة هنا هي الملفات المرتبطة بدعم حالة `PENDING`، وجدول `setup_candidates`، والتحقق من البيانات داخل Supabase.
- آخر تعديلات Telegram / Dashboard / Governor / Benchmark لا تحتاج عادةً **Migration SQL جديد مستقل** إذا كانت الجداول الأساسية مطبقة مسبقًا.
- إذا كان Supabase عندك قد طُبق عليه `SPRINT1_SUPABASE_MIGRATION.sql` و `SPRINT2_SETUP_STATE_MIGRATION.sql` بالفعل، فغالبًا لا تحتاج إعادة تطبيقهما إلا إذا كانت بيئة قاعدة البيانات ناقصة.
- ملف `supabase_schema_unified.sql` هو المخطط الموحّد الكامل، وليس بالضرورة مطلوبًا تنفيذه إذا كنت تستخدم المهاجرات الجزئية بالفعل.
