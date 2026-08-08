# دليل إنشاء فرع demo/mt5 ورفع ملفات المرحلة ١ — خطوة خطوة

## من موقع GitHub (بدون git على جهازك)
1. افتح مستودع Nabil-gold → الزر الذي يحمل اسم الفرع (يكتب "main") أعلى القائمة.
2. في خانة البحث اكتب: demo/mt5 ثم اضغط "Create branch: demo/mt5 from main".
3. الآن وأنت داخل الفرع الجديد: اضغط "Add file" → "Upload files".
4. ارفع ملفات الحزمة **بنفس المسارات** (المجلدات تُنشأ تلقائياً):
   - services/mt5_feed.py · services/mt5_executor.py · services/market_data.py ·
     services/database.py · services/telegram_bot.py
   - scripts/run_trade_updates.py · scripts/run_demo_loop.py · scripts/demo_watchdog.py
   - tests/mt5_fake.py · tests/test_mt5_phase1.py
   - config.json · .github/workflows/analyze.yml
5. Commit message: "demo/mt5: phase 1 feed+executor+tests".
6. كرر ذلك لأي ملف مستقبلي دائماً داخل الفرع demo/mt5 وليس main.

## إن كان عندك git على جهازك
git fetch origin
git checkout -b demo/mt5 origin/main
(انسخ الملفات أعلاه بنفس المسارات)
git add -A && git commit -m "demo/mt5: phase 1" && git push -u origin demo/mt5

## على VPS (بعد إنشاء الفرع)
1. ويندوز + MT5 + حساب ديمو + Python 3.11+ و pip install MetaTrader5 -r requirements.txt
2. git clone -b demo/mt5 https://github.com/nabiloashgaqr/Nabil-gold.git
3. ‎.env بالمتغيرات: EXECUTION_MODE=mt5_demo · TRADES_TABLE=trades_demo ·
   DATA_SOURCE primary=mt5 (داخل data_source في config الفرع أو env) ·
   MT5_PATH/MT5_LOGIN/MT5_PASSWORD/MT5_SERVER · TELEGRAM_DEMO_CHAT_ID ·
   وبقية الأسرار الموجودة.
4. نفّذ SQL إنشاء trades_demo وdemo_metrics (داخل الخطة التقنية الإنجليزية §4).
5. Task Scheduler: كل 5 دقائق python scripts/run_demo_loop.py ·
   وكل دقيقة python scripts/demo_watchdog.py.

## التحقق
python -m pytest -q → 1648+ ناجح · البطاقات تصل لشات الديمو prefixed 🧪 DEMO ·
أي عدم تطابق → 🧪 DEMO HALT وتوقف أوامر جديدة.
