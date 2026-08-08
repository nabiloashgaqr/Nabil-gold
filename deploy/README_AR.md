# حزمة المرحلة ٢ — بيئة VPS — 2026-08-08

## المحتوى
- deploy/trades_demo.sql: إنشاء trades_demo (نسخة كاملة من trades) +
  mt5_ticket/mt5_account/execution_mode + جدول demo_metrics. نفّذه مرة واحدة.
- deploy/.env.example: كل المتغيرات؛ انسخه إلى ‎.env على الـVPS وعبّئه.
- deploy/vps_setup.ps1: تثبيت بايثون والحزم (منها MetaTrader5)، نسخ .env،
  جدولة SS_DemoLoop (كل ٥ د) وSS_DemoWatchdog (كل دقيقة)، ثم اختبار الدخان.
- deploy/PHASE2_VPS_SETUP_EN.md: ورقة تنفيذ إنجليزية دقيقة للوكيل.
- scripts/demo_smoke_test.py: يفحص env + دخول MT5 (يرفض حساباً حقيقياً) +
  الشموع + قراءة trades_demo + رسالة 🧪 لشات الديمو؛ أي FAIL يوقف.

## الترتيب
استأجر ويندوز → MT5 ديمو → git clone -b demo/mt5 → نفّذ vps_setup.ps1 →
عبّئ .env → نفّذ trades_demo.sql → SMOKE OK → اترك الجدولة تعمل.

لا يُلمس main؛ ولا يُستخدم حساب حقيقي في هذه المرحلة إطلاقاً.
