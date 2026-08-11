# جولة إصلاح تطابق Telegram مع MT5 — 2026-08-11

## النتيجة الجنائية من تقرير السيرفر

عند الساعة 09:18 UTC كانت صورة MT5 **مطابقة للحالة الحالية**: قاعدة البيانات
تحتوي `0` صفوف نشطة، وMT5 يحتوي `0` مراكز و`0` أوامر. لكن التقرير أثبت
انفصالات خطيرة حدثت قبل ذلك:

1. الأمر المعلق عند `4417.24` وُضع فعلاً على MT5 بالتذكرة `404345964`، ثم
   أُرسلت بطاقة الوضع مرتين للتذكرة نفسها، وبعد أن خرج صفه من الكتاب ألغاه
   مدير التكات. لذلك غيابه الآن ليس دليلاً أنه لم يوضع؛ الخلل كان تكرار البطاقة
   وفقدان/تأخر مرآة الحالة.
2. الصفقة `...63e1f591` أُعلنت بدخول `4353.03` لكن تعبئة الوسيط الحقيقية كانت
   `4342.36`. ظل الكتاب يحمل السعر المخطط، فصار حساب الربح والرسائل غير مطابق.
3. الصفقة نفسها أغلقت TP1 مرتين: `0.05` ثم `0.02`، وبقي `0.03`. السبب أن صف
   الذاكرة المؤقتة لم يكن يُحدّث فور التنفيذ، ومع الكتاب المحلي غير المقفول أمكن
   إعادة تنفيذ الإغلاق الجزئي.
4. `tick_heartbeat.json` كان صفراً بايت بسبب استخدام `timezone` بلا استيراد.
   والـ watchdog كان ينهار كل دقيقة بـ `JSONDecodeError`، فلا يحيي المنفذ.
5. سجل tick manager وصل إلى نحو 40 MB بسبب إعادة رفض broker أربع مرات في
   الثانية (`Unsupported filling mode`). آلية fallback الحالية نجحت لاحقاً في
   وضع الأمر، لكن إعادة المحاولة بلا تهدئة كانت تملأ السجل.
6. مهمة `SS_MT5Terminal` سجلت `0x80070002` بسبب اقتباس مسار Program Files.
7. التخزين المحلي كان يكتب `trades.json` بطريقة truncate ثم write، بلا قفل بين
   التحليل ومدير التكات؛ وهذا يسمح بفقدان تحديث أو قراءة كتاب فارغ مؤقتاً.

## ما أصلحته هذه الجولة

- **Broker-first Telegram في mt5_demo:** التحليل يحفظ نية التنفيذ ولا يرسل
  بطاقة الصفقة. مدير التكات يرسل البطاقة الغنية فقط بعد وجود تذكرة MT5 حقيقية.
  في MARKET يُعرض سعر التعبئة الفعلي. إذا تعطل Telegram يعيد البطاقة دون إعادة
  الأمر. Paper mode لم يتغير.
- تحديث صف الذاكرة فور كل انتقال (ticket/fill/BE/TP1/trailing/close/cancel)، لمنع
  تكرار البطاقات أو تكرار نصف الإغلاق خلال مهلة تحديث الصفوف 3 ثوانٍ.
- منع TP1 ثانٍ أيضاً من **سجل صفقات الوسيط** حتى لو فقد الكتاب المحلي العلم.
- تصحيح سعر التنفيذ الحي: BUY يُدار على Bid وSELL على Ask، فلا يفعّل السبريد
  TP1/BE/trailing قبل بلوغ سعر الإغلاق الحقيقي.
- تثبيت actual fill والتذكرة من MT5، واستخدام `position_id` الصحيح في بطاقة
  الإغلاق.
- قفل بين العمليات + كتابة JSON ذرية + فشل مغلق عند فساد/ازدحام الكتاب.
- إصلاح heartbeat والـ watchdog، وإضافة جذر المشروع إلى `sys.path`.
- تهدئة إعادة وضع الأمر إلى مرة كل 5 ثوانٍ بدل 4 مرات/ثانية.
- إصلاح Golden Dual الذي كان يرسل البطاقة ثم يتخطى `save_trade`.
- منع صلب لأي تعديل وسيط إذا كان حساب MT5 من نوع REAL.
- منع PID قديم معاد الاستخدام من حجب العملية الصحيحة، ومنع رفع PID/heartbeat
  إلى المستودع مستقبلاً.
- إصلاح مهمة تشغيل MT5 عبر `deploy/tasks/mt5_terminal.bat`.
- فرض UTF-8 في جميع wrappers لإيقاف `UnicodeEncodeError` في المهام.
- تحديث smoke test لواقع التخزين المحلي وفحص حساب DEMO فعلياً.

**لم تتغير أي أرقام أو قوانين تداول:** stop/targets/150-40/الثقة/الجلسات كما هي.

## الاختبارات

```text
1752 collected
1749 passed
3 skipped
1 warning قديم (datetime.utcnow)
```

أضيفت 8 براهين جديدة للتزامن، heartbeat، watchdog، actual fill، منع TP1 المكرر،
منع REAL، ومنع ملفات runtime من الانتقال مع الكود.

## النشر على VPS

> لا تشغّل `run_tick_manager.py` يدوياً، ولا تستخدم `schtasks /Run` بالتوازي.

1. فك الحزمة فوق `C:\Nabil-gold` مع الاستبدال.
2. افتح PowerShell كمسؤول ونفّذ:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& "C:\Nabil-gold\deploy\apply_mt5_telegram_fix.ps1"
```

السكريبت:
- ينسخ `storage\trades.json` إلى backup مستقل.
- يفحص syntax للملفات الحرجة.
- يعيد تسجيل `SS_MT5Terminal` بالـ wrapper الصحيح.
- يحذف PID/heartbeat القديمة فقط.
- ينفذ `Restart-Computer -Force`، وهي طريقة إعادة التشغيل المعتمدة.

## التحقق بعد دقيقتين من عودة السيرفر

```powershell
cd C:\Nabil-gold
Get-Content .\tick_heartbeat.json -Raw
Get-Content .\heartbeat.json -Raw
Get-ScheduledTask SS_TickManager,SS_DemoLoop,SS_DemoWatchdog,SS_MT5Terminal |
  Select TaskName,State
Get-ScheduledTaskInfo SS_DemoWatchdog | Select LastRunTime,LastTaskResult
Get-Content .\logs\tick_manager.log -Tail 60
```

المطلوب:
- ملفا heartbeat يحتويان JSON غير فارغ.
- TickManager وDemoLoop في حالة Running.
- آخر نتيجة Watchdog تساوي `0`.
- لا يوجد سيل `Unsupported filling mode` أربع مرات في الثانية.
- أي بطاقة صفقة جديدة لا تصل إلا بعد قبول MT5، وتحمل البطاقة التنفيذية التذكرة.

بعدها شغّل smoke مرة واحدة (يرسل رسالة اختبار واحدة لقناة المشتركين):

```powershell
& "C:\Nabil-gold\deploy\tasks\demo_smoke.bat"
```

ثم أعد تشغيل ملف التشخيص السابق وارفع التقرير الجديد للمراجعة النهائية.
