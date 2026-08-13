"""Generate the detailed Arabic PDF plan for the MT5 demo migration phase."""
from __future__ import annotations

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph,
                                Spacer, Table, TableStyle)

ROOT = __file__.rsplit("/", 1)[0]
pdfmetrics.registerFont(TTFont("Amiri", f"{ROOT}/../fonts/Amiri-Regular.ttf"))
pdfmetrics.registerFont(TTFont("AmiriBold", f"{ROOT}/../fonts/Amiri-Bold.ttf"))

def ar(text: str) -> str:
    return get_display(arabic_reshaper.reshape(text))

TITLE = ParagraphStyle("t", fontName="AmiriBold", fontSize=20, leading=30,
                       alignment=2, textColor=colors.HexColor("#1a1a2e"))
H1 = ParagraphStyle("h1", fontName="AmiriBold", fontSize=15, leading=24,
                    alignment=2, spaceBefore=14, spaceAfter=6,
                    textColor=colors.HexColor("#16213e"))
H2 = ParagraphStyle("h2", fontName="AmiriBold", fontSize=12.5, leading=20,
                    alignment=2, spaceBefore=8, spaceAfter=4,
                    textColor=colors.HexColor("#0f3460"))
P = ParagraphStyle("p", fontName="Amiri", fontSize=11, leading=19,
                   alignment=2, spaceAfter=4)
B = ParagraphStyle("b", fontName="Amiri", fontSize=11, leading=18,
                   alignment=2, spaceAfter=2, rightIndent=14)
NOTE = ParagraphStyle("n", fontName="Amiri", fontSize=10, leading=16,
                      alignment=2, textColor=colors.HexColor("#555555"))

def bul(text: str) -> Paragraph:
    return Paragraph(ar("• " + text), B)

def tbl(rows, widths=None):
    data = [[Paragraph(ar(c), ParagraphStyle("c", fontName="Amiri", fontSize=10,
                                             leading=15, alignment=2)) for c in row]
            for row in rows]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16213e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, -1), "Amiri"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f7f7fb"), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t

story: list = [
    Paragraph(ar("الخطة التفصيلية للانتقال إلى مرحلة الديمو على MetaTrader 5"), TITLE),
    Paragraph(ar("SmartSignal / Nabil-gold · 2026-08-08 · وثيقة تنفيذية للموافقة"), NOTE),
    Spacer(1, 8),

    Paragraph(ar("أولاً: الرأي في فكرة الفرع"), H1),
    bul("نعم، الفرع (demo/mt5) داخل نفس المشروع هو القرار الصحيح: عزل كامل لكود التنفيذ عن الورقي، مع تاريخ مشترك وسهولة دمج لاحق عبر Pull Request."),
    bul("main يبقى مرجع الورقي ويعمل عبر GitHub Actions كما اليوم؛ فرع الديمو يعمل على VPS ولا يلمس main."),
    bul("قواعد الليلة (مصدر الحقيقة الموحد، الذهبي المزدوج، اتجاه الانجراف…) تُدمج في main أولاً، ثم يُقطع الفرع منها فيرث الديمو كل القواعد."),
    bul("كود التنفيذ يعيش خلف مفتاح execution_mode؛ قيمته في main = paper دائماً، وفي فرع الديمو = mt5_demo."),

    Paragraph(ar("ثانياً: مراحل النقل بالتفصيل"), H1),

    Paragraph(ar("المرحلة 0 — التمهيد (يومان)"), H2),
    bul("دمج حزم قواعد الليلة في main: مصدر الحقيقة الموحد، الاستثناء الذهبي، الانجراف الاتجاهي، جهة الأهداف، الأسباب الصادقة."),
    bul("تشغيل البوابة كاملة بعد الدمج (986+ اختبارات) والتأكد من خضرة main."),
    bul("فتح فرع demo/mt5 من main بعد الدمج مباشرة."),

    Paragraph(ar("المرحلة 1 — بنية الكود في الفرع (3-4 أيام)"), H2),
    bul("services/mt5_feed.py: مصدر أسعار بديل عند data_source=mt5؛ يحوّل شموع MT5 لنفس صيغة الشموع الحالية مع تحويل زمن الخادم إلى UTC، وخريطة رموز XAUUSD=XAU/USD، وعودة احتياطية لـ TwelveData عند انقطاع الطرفية."),
    bul("services/mt5_executor.py: إرسال الأوامر (market/limit)، الإغلاق الجزئي عند TP1، تعديل الوقف (تعادل/تريلنج) بقواعد الحمّال الموحد، وإعادة مطابقة كل دورة بين مراكز MT5 وصفقات Supabase؛ عند أي عدم تطابق: تنبيه تيليجرام + إيقاف أوامر جديدة فوراً."),
    bul("config: مفتاح execution_mode وأقسام mt5 (path/login/demo) تُقرأ من .env لا من المستودع."),
    bul("اختبارات جديدة بحقن وهمية لطبقة التنفيذ (رفض أمر، انزلاق، إغلاق جزئي غير مدعوم) تُضاف للبوابة."),

    Paragraph(ar("المرحلة 2 — بيئة VPS ويندوز (يومان)"), H2),
    bul("VPS ويندوز 2-4 أنوية / 8GB قرب خادم البروكر؛ تثبيت MT5 + حساب ديمو؛ Python 3.11+ وحزمة MetaTrader5."),
    bul(".env بالأسرار (Supabase/Telegram/LLM) + بيانات الديمو؛ لا أسرار في الفرع."),
    bul("git clone للفرع demo/mt5 على الخادم؛ التحديثات عبر git pull فقط."),
    bul("watchdog خدمة ويندوز: يفحص نبضة المحرك كل 5 دقائق ويرسل تنبيه تيليجرام عند التوقف + إعادة تشغيل تلقائية للطرفية."),

    Paragraph(ar("المرحلة 3 — تشغيل ظلّ وقياس (أسبوعان)"), H2),
    bul("الورقي على main (Actions) والديمو على VPS يعملان متوازيين على نفس القواعد."),
    bul("لوحة مقارنة يومية: الانزلاق، نسبة التعبئة، تطابق TP1/التريلنج/الإغلاق بين النظامين، أخطاء المطابقة، زمن الأوامر."),
    bul("لا يُتخذ أي قرار نقل قبل اكتمال أسبوعين وبيانات كافية."),

    Paragraph(ar("المرحلة 4 — شروط Go / No-Go"), H2),
    bul("صفر أخطاء مطابقة لمدة 5 أيام متتالية."),
    bul("متوسط انزلاق ضمن الحد المقبول (يُقترح ≤ 10 نقاط) ولا أمر خاطئ (حجم/اتجاه) إطلاقاً."),
    bul("موافقة مشغّل صريحة مكتوبة قبل أي خطوة تالية."),

    Paragraph(ar("المرحلة 5 — التوسع والدمج"), H2),
    bul("إضافة أزواج جديدة زوجاً واحداً في المرة (الذهب أولاً) بعد استقرار الديمو."),
    bul("بعد شهر مستقر: Pull Request لدمج كود التنفيذ في main مع بقاء execution_mode=paper افتراضياً؛ القرار الحي (حساب حقيقي) قرار منفصل لاحقاً."),

    Paragraph(ar("ثالثاً: المخاطر وتحصيناتها"), H1),
    tbl([
        ["الخطر", "التحصين"],
        ["انقطاع VPS/الطرفية", "watchdog + تنبيه تيليجرام + عودة تلقائية للورقي على main"],
        ["زمن MT5 ≠ UTC", "تحويل موحد مُختبر بحقن فروق ساعات"],
        ["بروكر لا يدعم الإغلاق الجزئي", "اختبار ديمو مبكر + خطة بديلة إغلاق كامل ثم أمر جديد"],
        ["أمر خاطئ", "سقف أوامر/يوم + حجم ثابت + إيقاف فوري عند عدم تطابق"],
        ["فقدان الاتصال بالبروكر أثناء صفقة", "الوقف/التريلنج عند البروكر يبقى حاكماً؛ المطابقة تصالح عند العودة"],
    ], widths=[6.2 * cm, 10.8 * cm]),
    Spacer(1, 8),

    Paragraph(ar("رابعاً: التراجع"), H1),
    bul("إيقاف خدمة VPS يعيد النظام كله للورقي على main خلال دقيقة؛ قاعدة البيانات واحدة (Supabase) فلا انقسام سجل."),
    bul("الفرع لا يُدمج في main إلا بـ Pull Request ومراجعة؛ الورقي محمي دائماً."),

    Paragraph(ar("خامساً: قائمة تحقق لكل مرحلة"), H1),
    tbl([
        ["المرحلة", "معيار القبول"],
        ["0 دمج القواعد", "البوابة خضراء على main"],
        ["1 بنية الكود", "اختبارات حقن التنفيذ خضراء"],
        ["2 البيئة", "نبضة watchdog حية + أمر ديمو تجريبي ناجح"],
        ["3 الظل", "تقرير مقارنة يومي لمدة 14 يوماً"],
        ["4 Go/No-Go", "5 أيام صفر أخطاء + موافقة مكتوبة"],
        ["5 التوسع", "PR مدمج + execution_mode=paper افتراضياً"],
    ], widths=[5.5 * cm, 11.5 * cm]),
    Spacer(1, 10),
    Paragraph(ar("لا يُنفذ أي بند قبل موافقة المشغّل على هذه الوثيقة."), NOTE),
]

doc = BaseDocTemplate("MT5_DEMO_MIGRATION_PLAN_AR.pdf", pagesize=A4,
                      title="MT5 Demo Migration Plan")
doc.addPageTemplates([PageTemplate(frames=[Frame(1.6 * cm, 1.4 * cm,
                                             A4[0] - 3.2 * cm, A4[1] - 2.8 * cm)])])
doc.build(story)
print("PDF OK")
