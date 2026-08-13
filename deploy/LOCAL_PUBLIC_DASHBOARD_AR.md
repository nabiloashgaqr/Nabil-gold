# لوحة عامة مستضافة محليًا بالكامل على VPS

## النتيجة

بعد هذه الجولة يصبح الموقع والبيانات في مكان واحد:

```text
أي زائر ترسل له الرابط
    ↓ HTTP port 8787
Windows VPS: SS_DashboardAPI
    ├─ الواجهة: C:\Nabil-gold\dashboard\
    └─ البيانات: C:\Nabil-gold\storage\trades.json
```

لا يعتمد وقت التشغيل على GitHub أو Vercel أو Supabase أو CDN. مكتبة Chart.js والشعار
موجودان داخل مجلد `dashboard/assets`.

الموقع **للقراءة فقط**:

- الخادم يقبل GET فقط.
- لا توجد أي واجهة لفتح/إغلاق/تعديل الصفقات.
- بيانات الأسرار والخريطة و`signal_snapshot` لا تخرج من VPS.
- المعروض فقط allowlist آمنة: الصفقة، حالتها، الأسعار، النتائج والإحصاءات.

## التثبيت

1. فك حزمة الجولة فوق `C:\Nabil-gold` مع الاستبدال.
2. افتح PowerShell كمسؤول:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
& "C:\Nabil-gold\deploy\setup_local_dashboard.ps1"
```

السكريبت يفحص Python، يفتح منفذ Windows Firewall `8787`، يسجل مهمة
`SS_DashboardAPI` عند الدخول، يحفظ الرابط، ثم ينفذ إعادة التشغيل المعتمدة.

## الرابط

بعد عودة VPS:

```powershell
Get-Content C:\Nabil-gold\dashboard_public_url.txt
```

سيظهر رابط شبيهًا بـ:

```text
http://VPS_PUBLIC_IP:8787/
```

أرسله لأي شخص تريد أن يشاهد اللوحة.

## التحقق

```powershell
Get-ScheduledTask SS_DashboardAPI | Select TaskName,State
Get-Content C:\Nabil-gold\logs\dashboard_api.log -Tail 40
Invoke-RestMethod http://127.0.0.1:8787/health
```

المطلوب:

```text
SS_DashboardAPI  Running
ok      : True
source  : vps-local-json
```

ثم افتح محليًا داخل VPS:

```text
http://127.0.0.1:8787/
```

ومن هاتف/جهاز خارجي افتح الرابط العام المحفوظ. إذا عمل محليًا ولم يعمل خارجيًا،
فالسبب جدار حماية إضافي في لوحة Contabo ويجب السماح بـInbound TCP 8787 هناك.

## إيقاف الاعتماد القديم

بعد نجاح الرابط المحلي العام:

- عطّل Workflow `Dashboard` في GitHub.
- أوقف مهمة `dashboard.yml` في cron-job.org.
- يمكن حذف/تجاهل مشروع Vercel؛ لم يعد جزءًا من التشغيل.
- أبقِ `SS_Dashboard` اليومية إن أردت بطاقة ملخص Telegram؛ الموقع الحي نفسه يعتمد
  على `SS_DashboardAPI` وليس على التوليد اليومي.

## ملاحظة HTTPS

هذا هو المسار الأبسط ويستخدم HTTP. يمكن إضافة HTTPS لاحقًا بدومين تملكه على
المنفذين 80/443 من دون تغيير مصدر البيانات أو إعادة GitHub/Vercel.
