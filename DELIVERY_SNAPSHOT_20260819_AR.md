# 📦 تقرير تسليم السنابشوت الكامل — Nabil-gold — 2026-08-19

**الملف:** `Nabil-gold-SNAPSHOT-20260819.zip` (4.3 MB · 608 ملفاً)
**الأساس:** `Nabil-gold-SNAPSHOT-20260818.zip` + كل أبنية اليوم (1 → 12) + التصغيرات النهائية
**التحقق:** مجموعة الاختبارات كاملة داخل هذه النسخة قبل الضغط — **2272 نجاح / 3 تخطٍّ / 0 فشل**

---

## 1) ما يختلف عن سنابشوت الأمس (قائمة الأبنية الكاملة)

| البناء | المحتوى | الحالة على الـVPS |
|---|---|---|
| **المرحلة 1** | طبقة ريجيم D1 + بوابة RANGE · حارس ارتباط XAU↔WTI · قاطع الهامش · حد خسارة يومي بالدولار · VaR/CVaR/Sharpe · Walk-Forward · ADX Wilder قانوني · إصلاح 999→3 | ✅ مطبق |
| **المرحلة 2** | SentimentAgent (COT+GLD) · VolatilityAgent (VIX/GVZ/OVX) · OilMacroAgent (EIA+OPEC+crack) · آلية ترقية auction (مغلقة) | ✅ مطبق |
| **إصلاحات الوكلاء** | Stochastic+Ichimoku · هارمونيك Gartley/Bat/Butterfly/Crab · H&S+أعلام · مستهلك الروزنامة في Macro · MAE/MFE بمضاعفات R · استهداف تقلب (مغلق) · مراقبة موارد | ✅ مطبق |
| **إصلاحات متبقية** | إبطال كاش Gemini عند قفزات التقلب · بريد احتياطي · فحص MT5 · قياس الارتباط مفعّل · أرشفة النسخ الجذرية | ✅ مطبق |
| **البناء 7** | قتل المسار 1 غير المؤكد (بياناتك: PF 0.95 الخاسر الوحيد) — قابل للعكس بمفتاح | ✅ مطبق |
| **البناء 8** | تصفير سقف الأوامر اليومي منتصف الليل + إغلاق أشباح السوق + قسم EXECUTION في الفحص | ✅ مطبق |
| **البناء 9** | أوامر المشغل: بوابات المشاهدة مغلقة (دخول طبيعي ذهب+نفط) · تأكيد النفط من oil_macro≥55 · ترقية WTI من الظل للحي | ✅ مطبق |
| **البناء 10** | إعادة القياس الخلفي (ذهب+نفط) · طبقة crack تلقائية لماكرو النفط · fill_oil_macro.py (EIA) | ✅ مطبق |
| **البناء 11** | طبقة المفاجآت الحقيقية مفعّلة (CPI/NFP/FOMC) · fill_economic_events.py · مهمة الماكرو كل 30 دقيقة | ✅ مطبق |
| **البناء 12** | حارس المعلقات بقاعدة المشغل: لا إلغاء بالانحراف · القتل فقط للمعارضين/أطروحة أفضل/اختراق الإبطال/العمر + ختم السبب والرمز ووقت الإغلاق | ✅ مطبق |

## 2) محتويات السنابشوت

```
Nabil-gold-CLEAN/
├── config.json                    ← كل الأقسام الجديدة (watch_gates, oil confirmer,
│                                    pending_freshness, macro_economic_events, ...)
├── agents/                        ← 16 وكيلاً (الخمسة + auction/macro + الثلاثة الجدد + المديرون)
├── services/                      ← +9 خدمات جديدة (market_regime, correlation_exposure,
│                                    risk_analytics, margin_guard, aux_market_data,
│                                    thesis_consensus المحدّث, session_planner المحدّث, ...)
├── scripts/                       ← +10 أدوات (تشخيص/قياس/فحص/تدقيق/تعبئة)
├── tests/                         ← 2275 اختباراً (2245 جديد + 30 معدلاً) — 2272 نجاح
├── storage/                       ← قوالب النفط والروزنامة (.example) بلا أسرار ولا سجلات
├── deploy/                        ← سكربتات التطبيق (ASCII+BOM) + التوثيق العربي
├── RUN_DIAGNOSTICS.ps1            ← الفحوص السبعة بأمر واحد وسجل واحد
└── SETUP_MACRO_30MIN.ps1          ← مهمة الماكرو كل 30 دقيقة (تتحقق من الموجود أولاً)
```

## 3) التنصيب من الصفر على جهاز جديد

```powershell
# 1) فك الضغط إلى C:\Nabil-gold
Expand-Archive -Path "$env:USERPROFILE\Downloads\Nabil-gold-SNAPSHOT-20260819.zip" -DestinationPath C:\ -Force

# 2) الأسرار (انسخ .env.template → .env واملأ كل القيم):
#    TELEGRAM_BOT_TOKEN / GEMINI_API_KEY / EIA_API_KEY (اختياري) / MT5 بيانات

# 3) التبعيات:
pip install -r requirements.txt

# 4) التحقق الكامل:
python -m pytest tests -q        # المتوقع: 2272 passed, 3 skipped

# 5) مهمة الماكرو كل 30 دقيقة (تكتشف/تنشئ بنفسها):
powershell -ExecutionPolicy Bypass -File SETUP_MACRO_30MIN.ps1

# 6) التعبئة الأولى للروزنامة:
python scripts\fill_economic_events.py --sync
```

## 4) قائمة مراجعة المشغل بعد التنصيب

| # | البند | الأمر / الملف |
|---|---|---|
| 1 | حارس الارتباط: enforce قبل أي توسع | `config.json → correlation_guard.enforce` |
| 2 | ملفات النفط الأسبوعية | `python scripts\fill_oil_macro.py` (بعد EIA_API_KEY) أو يدوياً |
| 3 | بعد كل إصدار اقتصادي | `python scripts\fill_economic_events.py --add ...` |
| 4 | أي معلقة أُلغيت؟ | `python scripts\pending_cancel_audit.py --date <يوم>` — السبب+الرمز+المدة |
| 5 | ما يعمل الآن؟ | `python scripts\inspect_running_modes.py` |
| 6 | الفحص الشامل | `powershell -File RUN_DIAGNOSTICS.ps1 -Days 7` |
| 7 | بوابة القرارات M1-M4 | `python scripts\verify_redesign_readiness.py --days 7` |
| 8 | التراجع عن أي بناء | `deploy\backups\patch_<وقت>\` على الـVPS الأصلي |

## 5) ما أُزيل عمداً من السنابشوت (مثل النسخة الأصلية)
- الأسرار (.env حقيقي) · ملفات وقت التشغيل (heartbeats/pid) · قواعد البيانات المحلية المتراكمة · مجلدات الكاش (correlation/aux_cache/sentiment) · سجلات التشخيص · النسخ الاحتياطية
- **الاستثناء الوحيد:** `storage/macro/economic_events.json` بُقيت فارغة `[]` عمداً — القيم الفعلية تُبنى بـ`--sync`/`--add` على الجهاز (لا تصنيع بيانات أبداً)

## 6) بصمة الحالة

| البند | القيمة |
|---|---|
| اختبارات | **2272 نجاح / 3 تخطٍّ / 0 فشل** (المتخطية: node غير منصّب + مؤرشفة موثقة) |
| ملفات | 608 |
| الحجم | 4.3 MB مضغوط |
| السكربتات | ASCII + BOM (تعمل PS 5.1 و 7) |
| الأسرار في الحزمة | **صفر** (تحقق بالفحص الآلي) |
