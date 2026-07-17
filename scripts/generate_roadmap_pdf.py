from __future__ import annotations

from pathlib import Path

import arabic_reshaper
from bidi.algorithm import get_display
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1]
OUT_PDF = ROOT / "Nabil_Gold_Roadmap_AR.pdf"
OUT_MD = ROOT / "Nabil_Gold_Roadmap_AR.md"

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def ar(text: str) -> str:
    text = str(text or "")
    if not text.strip():
        return ""
    return get_display(arabic_reshaper.reshape(text))


def rtl_para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(ar(text).replace("\n", "<br/>") , style)


def code_para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"), style)


def build_markdown() -> str:
    return """# خطة تطوير شاملة لنظام Nabil Gold

## ملخص تنفيذي
هذه الوثيقة تضع خارطة طريق عملية لتحويل النظام من **Consensus Engine** إلى **Setup Engine** قادر على التقاط نفس الفرص التي يراها المحلل اليدوي القوي، مع الاحتفاظ بانضباط النظام وقابلية القياس والتحسين المستمر.

الهدف الواقعي ليس التفوق الحرفي على كل المحللين، بل بناء نظام:
- أدق من أغلب المحللين العاديين
- أكثر اتساقًا وانضباطًا من التداول اليدوي
- قادر على التقاط setups من نوع sweep → displacement → POI → mitigation
- قابل للتقييم والتحسين بالبيانات وليس بالانطباع

## التشخيص الحالي
### ما الذي يجيده النظام اليوم؟
- بنية منظمة متعددة الوكلاء
- إدارة مخاطر وفلاتر أمان جيدة
- حفظ صفقات وحالة تشغيل وتقارير
- Telegram وSupabase وDashboard وLearning
- منع البيانات الوهمية في الإنتاج وحماية نسبية من الفوضى

### أين الضعف الحقيقي؟
- النظام يفكر Snapshot-by-Snapshot أكثر من التفكير كسيناريو سعري متسلسل
- الدخول ما زال أقرب إلى Market-heavy execution من Zone execution الحقيقي
- SMC edge موجود لكنه ليس قائد القرار في ظروفه الطبيعية
- نفس عتبات القرار تستخدم تقريبًا لكل setup مهما اختلف السياق
- التعلم موجود لكنه ليس Context-aware بالقدر الكافي
- الإدارة بعد الدخول جيدة عمومًا لكنها ليست Structure-aware بما يكفي

## الرؤية المعمارية الجديدة
يجب إعادة تنظيم عقل النظام إلى ست طبقات:
1. Data + Observability
2. Setup Detection
3. Setup State Machine
4. Strategy-specific Decisioning
5. Structure-aware Execution & Trade Management
6. Regime-aware Learning + Analyst Distillation

---

## المرحلة 0 — تثبيت خط الأساس Baseline
**المدة المقترحة:** 3–5 أيام

### الهدف
فهم أين يخسر النظام وأين يتأخر وما هي setups التي يفوتها قبل تعديل أي منطق.

### لماذا هذه المرحلة ضرورية؟
أي تطوير بدون baseline واضح سيتحول إلى تعديل عشوائي لا يمكن قياس أثره.

### الأعمال المطلوبة
- إضافة مقاييس جديدة تتجاوز win rate وPnL:
  - expectancy
  - avg R achieved
  - RR capture %
  - missed setup rate
  - late entry distance
  - entry efficiency
  - MFE / MAE
  - performance by setup/session/regime/lead-agent
- توسيع تخزين الصفقة ليشمل:
  - setup_type
  - setup_id
  - lead_agent
  - entry_profile
  - poi_type
  - sweep_side
  - displacement_score
  - setup_quality
  - miss_reason
- بناء breakdowns في dashboard والتقارير حسب setup/session/regime

### الملفات ذات الأولوية
- `services/database.py`
- `supabase_schema_unified.sql`
- `services/performance_dashboard.py`
- `services/backtesting.py`
- `scripts/run_backtest.py`
- `dashboard/` و `scripts/generate_dashboard.py`

### الاختبارات المطلوبة
- `tests/test_performance_dashboard.py`
- `tests/test_phase5_data_enrichment.py`
- `tests/test_learning_service.py`

### مخرجات النجاح
لوحة واضحة تقول مثلًا:
- Reversal setups misses = 42%
- MARKET entries أسوأ من zone entries بـ 0.8R
- أفضل session = London continuation
- أسوأ session = NY reversal بدون sweep confirmation

---

## المرحلة 1 — إصلاح طبقة الدخول Execution Layer
**المدة:** 1–2 أسبوع

### الهدف
تحويل التنفيذ من منطق market-heavy إلى zone/pending execution حقيقي.

### التشخيص المرتبط بالكود
في `config.json` و `agents/risk_management_agent.py` و `agents/open_trades_manager.py` توجد بنية تسمح بـ LIMIT/STOP/PENDING، لكنها غير مستغلة استغلالًا صحيحًا لأن التهيئة الحالية تدفع كثيرًا نحو MARKET.

### الأعمال المطلوبة
1. إعادة تفعيل execution profile حقيقي:
   - `entry_style = smart` أو `hybrid` أو profile جديد مثل `poi_pending`
2. إلغاء أثر `pending_support_removed: true`
3. توسيع `_smart_entry()` داخل `agents/risk_management_agent.py` ليحدد بوضوح متى نستخدم:
   - MARKET
   - LIMIT
   - STOP
4. تعريف zone object موحد يحتوي:
   - proximal
   - distal
   - fill_rule
   - invalidation anchor
   - source
5. في `scripts/run_analysis.py`:
   - حفظ إشارات pending
   - replace/cancel للإشارات القديمة لنفس setup
6. في `services/telegram_bot.py`:
   - إظهار zone boundaries
   - نوع الدخول
   - invalidation
   - target liquidity

### الملفات ذات الأولوية
- `config.json`
- `agents/risk_management_agent.py`
- `agents/open_trades_manager.py`
- `scripts/run_analysis.py`
- `services/database.py`
- `services/telegram_bot.py`

### الاختبارات المطلوبة
- `tests/test_limit_fill_logic.py`
- `tests/test_pending_zone_execution.py`
- `tests/test_no_phantom_fill.py`
- `tests/test_pending_replace_cancel.py`

### معيار النجاح
- تحسن entry efficiency
- تحسن planned RR
- تقليل التأخر في الدخول
- تقليل الصفقات التي تدخل بعد ضياع الميزة الأصلية

---

## المرحلة 2 — بناء Setup State Machine
**المدة:** 2–3 أسابيع

### الهدف
تحويل النظام من قراءة لحظية إلى منطق يحتفظ بالقصة السعرية عبر الزمن.

### الفكرة الأساسية
المحلل اليدوي يرى غالبًا التسلسل التالي:
- sweep
- displacement
- MSS/CHOCH
- تحديد POI
- انتظار mitigation
- trigger
- invalidation أو entry

### ما الذي يجب بناؤه؟
إضافة مكون جديد مثل:
- `services/setup_memory.py`
أو
- `agents/setup_state_agent.py`

### الحالات المقترحة
- DETECTED
- SWEEP_CONFIRMED
- DISPLACEMENT_CONFIRMED
- MSS_CONFIRMED
- POI_MARKED
- MITIGATION_PENDING
- ENTRY_ARMED
- ENTRY_TRIGGERED
- INVALIDATED
- EXPIRED

### تطوير `agents/smc_agent.py`
يجب أن يتحول من signal provider إلى setup builder يخرج:
- sweep side
- sweep quality
- equal highs/lows
- session high/low raid
- displacement score
- MSS/CHOCH quality
- order block rank
- FVG rank
- premium/discount score
- mitigation status
- target liquidity map

### تطوير `scripts/run_analysis.py`
- تحديث setup state بدل فقط اتخاذ قرار فوري
- ربط كل setup بمعرّف ثابت
- تأجيل الإشارة حتى transition صحيح

### تطوير قاعدة البيانات
إضافة جداول:
- `setup_candidates`
- `setup_state_events`

### الاختبارات المطلوبة
- `tests/test_setup_state_machine.py`
- `tests/test_sweep_to_mitigation_flow.py`
- `tests/test_poi_ranking.py`
- `tests/test_invalidated_setup_cleanup.py`

### معيار النجاح
أن يستطيع النظام القول:
> لدي liquidity reversal setup في حالة MITIGATION_PENDING
بدلًا من مجرد القول:
> لدي 3 وكلاء موافقين الآن.

---

## المرحلة 3 — قرار مختلف لكل نوع Setup
**المدة:** 2 أسبوع

### الهدف
عدم استخدام نفس منطق القرار لكل أنواع الصفقات.

### المشكلة الحالية
`agents/decision_agent.py` يعمل كـ generic weighted consensus ممتاز، لكنه لا يميز بما يكفي بين:
- liquidity reversal
- continuation pullback
- range fade
- breakout continuation

### التعديل البنيوي
إضافة strategy profiles مثل:
1. `liquidity_reversal`
2. `trend_pullback`
3. `range_fade`

لكل profile نحدد:
- lead agent
- supporting agents
- soft veto / hard veto
- min agents
- min confidence
- allowed execution mode
- management profile
- session preference
- daily bias requirement
- macro/news strictness

### الملفات ذات الأولوية
- `agents/decision_agent.py`
- `config.json`
- ملف جديد مقترح: `services/strategy_profiles.py`

### أمثلة منطقية
#### Liquidity Reversal
- SMC + Price Action = core
- MTF = confirm
- Technical/Classical = supportive only

#### Trend Pullback
- MTF + Classical = core
- Price Action = confirm
- SMC = supportive

### الاختبارات المطلوبة
- `tests/test_strategy_profiles.py`
- `tests/test_reversal_consensus.py`
- `tests/test_continuation_consensus.py`
- `tests/test_soft_veto_logic.py`

### معيار النجاح
عدم قتل setup SMC ممتاز فقط لأنه لم يمر بنفس قمع الإجماع التقليدي لكل شيء.

---

## المرحلة 4 — إدارة المخاطر والإدارة بعد الدخول بشكل Structure-aware
**المدة:** 2 أسبوع

### الهدف
تخصيص الوقف والأهداف والإدارة حسب نوع setup والبنية السعرية، لا حسب أرقام عامة فقط.

### التشخيص الحالي
`agents/risk_management_agent.py` و `agents/open_trades_manager.py` جيدان جدًا كإدارة عامة، لكن يلزمهما تخصيص حسب:
- reversal setup
- continuation setup
- range setup

### الأعمال المطلوبة
#### في `agents/risk_management_agent.py`
- SL خلف sweep extreme / distal edge / invalidation candle في reversal
- SL خلف pullback structure في continuation
- TP map مرتبط بـ internal / external liquidity وليس ATR فقط
- risk policy snapshot لكل صفقة

#### في `agents/open_trades_manager.py`
إضافة management profiles:
- reversal_profile
- continuation_profile
- range_profile

لكل profile:
- breakeven trigger
- partial logic
- trail mode
- expiry policy

### ملاحظة مهمة بخصوص scale-in
يفضل مؤقتًا:
- تعطيله على reversal setups
- أو حصره على continuation فقط إلى أن يثبت الأساس

### الملفات ذات الأولوية
- `agents/risk_management_agent.py`
- `agents/open_trades_manager.py`
- `services/database.py`
- `config.json`
- `services/telegram_bot.py`

### الاختبارات المطلوبة
- `tests/test_structure_aware_sl.py`
- `tests/test_target_liquidity_mapping.py`
- `tests/test_management_profiles.py`
- `tests/test_scalein_continuation_only.py`

### معيار النجاح
- انخفاض stop-outs غير الضرورية
- تحسن RR capture
- تحسن actual R achieved
- تقليل الخروج المبكر من الصفقات الجيدة

---

## المرحلة 5 — تعلم حقيقي حسب السياق Regime-aware Learning
**المدة:** 2–3 أسابيع

### الهدف
تحويل `services/learning_service.py` من توصيات عامة إلى تعلم يفرّق بين السياقات.

### المشكلة الحالية
الخدمة الحالية مفيدة، لكنها لا تجيب بدقة عن سؤال:
> أي agent أو profile يعمل أفضل في أي setup وأي session وأي regime؟

### الأعمال المطلوبة
1. بناء contextual weight matrix حسب:
   - setup type
   - session
   - regime
   - daily bias alignment
   - lead agent
2. جعل `DecisionAgent` يقرأ weight profile مناسب بدل أوزان ثابتة دائمًا
3. تفعيل dynamic risk لاحقًا بشكل contextual وليس global فقط
4. إظهار نتائج التعلم الجديدة في dashboard والتقارير

### مثال أوزان سياقية
#### إذا setup = liquidity_reversal
- smc 35%
- price_action 25%
- mtf 20%
- classical 10%
- technical 10%

#### إذا setup = continuation
- mtf 30%
- classical 25%
- price_action 20%
- smc 15%
- technical 10%

### الملفات ذات الأولوية
- `services/learning_service.py`
- `agents/decision_agent.py`
- `config.json`
- `services/performance_dashboard.py`

### الاختبارات المطلوبة
- `tests/test_contextual_weights.py`
- `tests/test_regime_learning_profiles.py`
- `tests/test_dynamic_risk_by_setup.py`

### معيار النجاح
- انخفاض drawdown
- تحسن expectancy
- تحسن الأداء داخل أفضل السياقات
- تقليل التعميم الخاطئ للأوزان

---

## المرحلة 6 — استنساخ Edge المحلل Analyst Distillation
**المدة:** 3–4 أسابيع ثم تستمر بشكل دائم

### الهدف
تحويل خبرة المحلل اليدوي الأفضل لديك إلى features وقواعد ومقاييس قابلة للاختبار.

### الأعمال المطلوبة
1. بناء dataset يدوي أو نصف يدوي باسم مثل:
   - `analyst_labels`
2. تخزين الحقول التالية لكل مثال:
   - timestamp
   - bias
   - setup_type
   - sweep_side
   - poi_type
   - poi_quality_grade
   - intended_entry
   - invalidation
   - targets
   - trade/no-trade
   - result
3. بناء أداة مقارنة:
   - bot vs analyst في نفس اليوم ونفس الشارت
4. بناء Analyst Quality Scorer يعتمد على:
   - sweep quality
   - displacement strength
   - POI freshness
   - HTF alignment
   - session quality
   - target clarity
   - invalidation clarity

### الملفات المقترحة
- `services/analyst_distillation.py`
- `scripts/compare_analyst_vs_bot.py`
- `services/database.py`
- dashboard sections جديدة للمقارنة

### الاختبارات المطلوبة
- `tests/test_analyst_overlap_metrics.py`
- `tests/test_quality_score_alignment.py`

### معيار النجاح
أن يصبح bot قادرًا على رؤية ما يراه المحلل في نسبة متزايدة من الأمثلة، ويدخل قرب نفس المنطقة، ويمنع كثيرًا من الصفقات التي كان المحلل سيتجنبها.

---

## المرحلة 7 — تحسين الدقة التنفيذية Micro-Precision
**المدة:** اختيارية بعد المراحل السابقة

### الهدف
رفع الدقة في التنفيذ حتى يقترب النظام من العين البشرية في الـ microstructure.

### الأعمال المطلوبة
- إضافة M1/M3 للتنفيذ فقط
- إضافة session microstructure logic:
  - London open sweep
  - NY open sweep
  - lunch drift filters
  - pre-news fake move
- إضافة مصدر بيانات أدق أو fallback أقوى للتنفيذ

### معيار النجاح
تقليل lag في التنفيذ وتحسين دقة trigger داخل zone.

---

## الترتيب الزمني المقترح
### الشهر الأول
- المرحلة 0
- المرحلة 1
- بداية المرحلة 2

### الشهر الثاني
- إكمال المرحلة 2
- المرحلة 3
- جزء من المرحلة 4

### الشهر الثالث
- إكمال المرحلة 4
- المرحلة 5
- بداية المرحلة 6

---

## الملفات الأعلى أولوية في المشروع
### أولوية قصوى
- `scripts/run_analysis.py`
- `agents/smc_agent.py`
- `agents/decision_agent.py`
- `agents/risk_management_agent.py`
- `agents/open_trades_manager.py`
- `services/database.py`
- `config.json`

### أولوية ثانية
- `services/learning_service.py`
- `services/performance_dashboard.py`
- `services/telegram_bot.py`
- `dashboard/*`

---

## أول Sprint مقترح
1. إضافة `setup_type` و `lead_agent` و `setup_quality` إلى snapshot الصفقة
2. إعادة تفعيل pending/zone execution الحقيقي
3. تطوير Telegram message لإظهار zone/invalidation/target liquidity
4. إضافة `setup_candidates` table
5. توسيع `smc_agent.py` لإخراج sweep/displacement/POI structure
6. بناء state machine أولي
7. فصل 3 strategy profiles
8. ربط thresholds بالـ profile
9. إضافة تقارير by setup/session/regime
10. قياس الفرق بين MARKET وZONE entries

---

## مؤشرات الأداء الرئيسية KPI المقترحة
- Win rate
- Expectancy
- Avg R achieved
- RR capture %
- Entry efficiency
- Late entry distance
- Missed setup rate
- Setup completion rate
- Setup invalidation rate
- Performance by session
- Performance by regime
- Performance by setup family
- Performance by lead agent

---

## ما الذي لا يجب فعله الآن؟
- لا تضف مؤشرات جديدة بكثرة
- لا تجعل Gemini صاحب القرار النهائي
- لا توسع النظام لأصول كثيرة قبل إتقان الذهب
- لا تفعّل auto-learning المباشر قبل وجود baseline واضح
- لا تعقّد scale-in قبل إصلاح execution وsetup state machine

---

## الخلاصة النهائية
التحول المطلوب ليس مجرد تحسين thresholds، بل تغيير فلسفة النظام من:
**مؤشرات + إجماع + فلترة**
إلى:
**Stateful SMC Setup Engine + Strategy Profiles + Zone Execution + Contextual Learning**

إذا نُفذت هذه الخارطة بالترتيب، فسيقترب النظام كثيرًا من منطق المحلل اليدوي القوي، مع ميزة إضافية مهمة: الانضباط، والقياس، والقدرة على التحسن المستمر.
"""


def build_pdf() -> None:
    pdfmetrics.registerFont(TTFont("DejaVu", FONT_REGULAR))
    pdfmetrics.registerFont(TTFont("DejaVuBold", FONT_BOLD))
    pdfmetrics.registerFont(TTFont("DejaVuMono", FONT_MONO))

    doc = SimpleDocTemplate(
        str(OUT_PDF),
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.4 * cm,
        title="Nabil Gold Roadmap Arabic",
        author="Arena.ai Agent Mode",
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleAR",
        parent=styles["Title"],
        fontName="DejaVuBold",
        fontSize=22,
        leading=30,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#111827"),
        spaceAfter=10,
    )
    subtitle_style = ParagraphStyle(
        "SubTitleAR",
        parent=styles["Normal"],
        fontName="DejaVu",
        fontSize=11,
        leading=16,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#4B5563"),
        spaceAfter=8,
    )
    h1 = ParagraphStyle(
        "H1AR",
        parent=styles["Heading1"],
        fontName="DejaVuBold",
        fontSize=16,
        leading=24,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#0F172A"),
        spaceBefore=10,
        spaceAfter=6,
    )
    h2 = ParagraphStyle(
        "H2AR",
        parent=styles["Heading2"],
        fontName="DejaVuBold",
        fontSize=13,
        leading=20,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#1D4ED8"),
        spaceBefore=6,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "BodyAR",
        parent=styles["Normal"],
        fontName="DejaVu",
        fontSize=10.5,
        leading=17,
        alignment=TA_RIGHT,
        textColor=colors.HexColor("#111827"),
        spaceAfter=3,
    )
    bullet = ParagraphStyle(
        "BulletAR",
        parent=body,
        rightIndent=10,
        spaceAfter=2,
    )
    code = ParagraphStyle(
        "CodeLTR",
        parent=styles["Code"],
        fontName="DejaVuMono",
        fontSize=8.8,
        leading=12,
        alignment=TA_LEFT,
        backColor=colors.HexColor("#F3F4F6"),
        borderPadding=4,
        textColor=colors.HexColor("#111827"),
        spaceAfter=3,
    )

    story = []
    story.append(Spacer(1, 0.7 * cm))
    story.append(rtl_para("خطة تطوير شاملة لنظام Nabil Gold", title_style))
    story.append(rtl_para("خارطة طريق عملية لتحويل النظام من Consensus Engine إلى Stateful Setup Engine", subtitle_style))
    story.append(rtl_para("إعداد: Arena.ai Agent Mode — تاريخ الإصدار: 2026-07-15", subtitle_style))
    story.append(Spacer(1, 0.4 * cm))

    info_table = Table(
        [
            [ar("الهدف"), ar("رفع دقة النظام، تحسين مكان الدخول، وبناء منطق Setup lifecycle")],
            [ar("النطاق"), ar("XAU/USD أولًا مع التركيز على SMC + execution + learning")],
            [ar("الأولوية"), ar("التحول من إجماع عام إلى استراتيجيات حسب نوع الفرصة")],
        ],
        colWidths=[4.0 * cm, 11.8 * cm],
        hAlign="RIGHT",
    )
    info_table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "DejaVu"),
                ("FONTNAME", (0, 0), (0, -1), "DejaVuBold"),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#111827")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E5EEFf")),
                ("BACKGROUND", (1, 0), (1, -1), colors.HexColor("#F8FAFC")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(info_table)
    story.append(PageBreak())

    def H1(text: str):
        story.append(rtl_para(text, h1))

    def H2(text: str):
        story.append(rtl_para(text, h2))

    def P(text: str):
        story.append(rtl_para(text, body))

    def B(text: str):
        story.append(rtl_para(f"— {text}", bullet))

    def C(text: str):
        story.append(code_para(text, code))

    H1("1) الملخص التنفيذي")
    P("هذه الوثيقة تضع خطة تطوير كاملة لنظام Nabil Gold بهدف تحويله من محرك إجماع عام إلى محرك setups ذكي، يحتفظ بالقصة السعرية، وينفذ من مناطق حقيقية، ويتعلم حسب نوع الفرصة والسياق.")
    P("الهدف الواقعي ليس التفوق الحرفي على كل المحللين في كل الظروف، بل بناء نظام أقوى من أغلب المحللين العاديين، وأكثر اتساقًا من التداول اليدوي، وقادر على التقاط الفرص التي يراها المحلل الجيد من نوع sweep → displacement → POI → mitigation.")

    H1("2) التشخيص الحالي")
    H2("ما الذي يجيده النظام اليوم؟")
    for item in [
        "بنية منظمة متعددة الوكلاء مع فصل واضح بين agents وservices وscripts.",
        "إدارة مخاطر وفلاتر تشغيلية جيدة نسبيًا مقارنة بكثير من المشاريع المماثلة.",
        "حفظ الحالة والصفقات والتقارير ووجود قاعدة بيانات ولوحة معلومات وتكامل Telegram.",
        "وجود أساس تقني للتعلم والتحليلات اللاحقة والتوسع المستقبلي.",
    ]:
        B(item)

    H2("أين الضعف الحقيقي؟")
    for item in [
        "النظام يفكر غالبًا كـ Snapshot Engine وليس Setup Lifecycle Engine.",
        "طبقة التنفيذ ما زالت تميل إلى MARKET أكثر من zone/pending execution الحقيقي.",
        "منطق SMC موجود لكنه ليس قائد القرار في ظروفه الطبيعية.",
        "عتبات القرار متقاربة جدًا بين أنواع setups المختلفة.",
        "التعلم الحالي مفيد لكنه ليس Context-aware بما يكفي.",
        "الإدارة بعد الدخول جيدة عمومًا لكنها ليست Structure-aware حسب نوع setup.",
    ]:
        B(item)

    H1("3) الرؤية المعمارية الجديدة")
    P("التحول المطلوب ليس مجرد تعديل thresholds، بل إعادة بناء عقل النظام على ست طبقات مترابطة:")
    for item in [
        "Data + Observability",
        "Setup Detection",
        "Setup State Machine",
        "Strategy-specific Decisioning",
        "Structure-aware Execution & Trade Management",
        "Regime-aware Learning + Analyst Distillation",
    ]:
        B(item)

    H1("4) المرحلة 0 — تثبيت خط الأساس Baseline")
    P("المدة المقترحة: 3–5 أيام")
    H2("الهدف")
    P("فهم أين يخسر النظام وأين يتأخر وما هي setups التي يفوتها قبل تعديل أي منطق.")
    H2("المطلوب")
    for item in [
        "إضافة مقاييس مثل expectancy وavg R achieved وRR capture % وentry efficiency وlate entry distance وmissed setup rate.",
        "توسيع trade snapshot ليحفظ setup_type وlead_agent وsetup_quality وpoi_type وsweep_side وdisplacement_score وmiss_reason.",
        "إظهار breakdowns حسب setup/session/regime/lead-agent في dashboard والتقارير.",
        "توسيع backtesting والتقارير حتى تعطي تحليلًا سببيًا لا مجرد رقم PnL نهائي.",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "services/database.py",
        "supabase_schema_unified.sql",
        "services/performance_dashboard.py",
        "services/backtesting.py",
        "scripts/run_backtest.py",
        "dashboard/ و scripts/generate_dashboard.py",
    ]:
        C(f)
    H2("معيار النجاح")
    P("لوحة تقول بوضوح أي setups تفشل، وأي sessions أفضل، وهل MARKET entries أسوأ من zone entries، وأين يتأخر النظام عن المحلل.")

    H1("5) المرحلة 1 — إصلاح طبقة الدخول Execution Layer")
    P("المدة: 1–2 أسبوع")
    H2("الهدف")
    P("الانتقال من تنفيذ market-heavy إلى zone/pending execution حقيقي.")
    H2("الأعمال المطلوبة")
    for item in [
        "إعادة تفعيل execution profile حقيقي عبر smart أو hybrid أو profile جديد مثل poi_pending.",
        "إلغاء أثر pending_support_removed: true أو إزالته من الإعدادات.",
        "تطوير _smart_entry داخل RiskManagementAgent ليقرر بوضوح بين MARKET وLIMIT وSTOP حسب نوع setup وقرب السعر من المنطقة.",
        "توحيد شكل zone object بحيث يحتوي proximal وdistal وfill_rule وinvalidation anchor وsource.",
        "حفظ إشارات pending في run_analysis مع replace/cancel للإشارات القديمة لنفس setup.",
        "تطوير Telegram message لإظهار نوع الدخول والمنطقة والـ invalidation والهدف السعري المرتبط بالسيولة.",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "config.json",
        "agents/risk_management_agent.py",
        "agents/open_trades_manager.py",
        "scripts/run_analysis.py",
        "services/database.py",
        "services/telegram_bot.py",
    ]:
        C(f)
    H2("معيار النجاح")
    P("تحسن ملحوظ في entry efficiency وplanned RR وتقليل الصفقات التي تدخل بعد ضياع الميزة الأصلية.")

    H1("6) المرحلة 2 — بناء Setup State Machine")
    P("المدة: 2–3 أسابيع")
    H2("الهدف")
    P("تحويل النظام من قراءة لحظية إلى منطق يحتفظ بالقصة السعرية عبر الزمن.")
    H2("الحالات المقترحة")
    for item in [
        "DETECTED",
        "SWEEP_CONFIRMED",
        "DISPLACEMENT_CONFIRMED",
        "MSS_CONFIRMED",
        "POI_MARKED",
        "MITIGATION_PENDING",
        "ENTRY_ARMED",
        "ENTRY_TRIGGERED",
        "INVALIDATED",
        "EXPIRED",
    ]:
        B(item)
    H2("تطوير SMC Agent")
    for item in [
        "إخراج sweep side وsweep quality وequal highs/lows وsession high/low raid.",
        "قياس displacement strength وMSS/CHOCH quality وorder block rank وFVG rank.",
        "تحديد premium/discount score وtarget liquidity map وmitigation status.",
    ]:
        B(item)
    H2("تطوير run_analysis وقاعدة البيانات")
    for item in [
        "تحديث setup state مع كل دورة بدل انتظار إجماع لحظي فقط.",
        "إضافة setup_id ثابت لكل سيناريو سعري.",
        "إضافة جداول setup_candidates وsetup_state_events.",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "agents/smc_agent.py",
        "scripts/run_analysis.py",
        "services/database.py",
        "services/setup_memory.py (ملف جديد مقترح)",
        "supabase_schema_unified.sql",
    ]:
        C(f)

    H1("7) المرحلة 3 — قرار مختلف لكل نوع Setup")
    P("المدة: 2 أسبوع")
    H2("الهدف")
    P("عدم استخدام نفس منطق القرار لكل أنواع الصفقات.")
    H2("الاستراتيجيات الأساسية المقترحة")
    for item in [
        "liquidity_reversal",
        "trend_pullback",
        "range_fade",
    ]:
        B(item)
    H2("لكل profile يجب تحديد")
    for item in [
        "lead agent وsupporting agents",
        "soft veto وhard veto",
        "min agents وmin confidence",
        "نوع التنفيذ المناسب",
        "session preference وdaily bias requirement",
        "management profile الملائم بعد الدخول",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "agents/decision_agent.py",
        "config.json",
        "services/strategy_profiles.py (ملف جديد مقترح)",
    ]:
        C(f)

    H1("8) المرحلة 4 — إدارة المخاطر والإدارة بعد الدخول بشكل Structure-aware")
    P("المدة: 2 أسبوع")
    H2("الهدف")
    P("تخصيص الوقف والأهداف والإدارة حسب نوع setup والبنية السعرية.")
    H2("التعديلات المطلوبة")
    for item in [
        "في reversal setup: SL خلف sweep extreme أو distal edge أو invalidation candle.",
        "في continuation setup: SL خلف pullback structure أو continuation OB.",
        "ربط الأهداف بالسيولة الداخلية والخارجية وليس ATR فقط.",
        "إضافة management profiles منفصلة داخل OpenTradesManager.",
        "حفظ management_profile وinvalidation_type وtarget_map مع كل صفقة.",
        "تقييد scale-in مؤقتًا على continuation setups فقط أو تعطيله حتى يثبت الأساس.",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "agents/risk_management_agent.py",
        "agents/open_trades_manager.py",
        "services/database.py",
        "config.json",
        "services/telegram_bot.py",
    ]:
        C(f)

    H1("9) المرحلة 5 — تعلم حقيقي حسب السياق Regime-aware Learning")
    P("المدة: 2–3 أسابيع")
    H2("الهدف")
    P("تحويل التعلم من توصيات عامة إلى تعلم يفرّق بين setup type وsession وregime وlead agent.")
    H2("التعديلات المطلوبة")
    for item in [
        "بناء contextual weight matrix حسب setup type وsession وregime وdaily bias alignment.",
        "جعل DecisionAgent يقرأ weight profile مناسب بدل أوزان ثابتة دائمًا.",
        "تفعيل dynamic risk لاحقًا بشكل contextual وليس global فقط.",
        "إظهار نتائج التعلم الجديدة في dashboard والتقارير اليومية والأسبوعية.",
    ]:
        B(item)
    H2("الملفات ذات الأولوية")
    for f in [
        "services/learning_service.py",
        "agents/decision_agent.py",
        "config.json",
        "services/performance_dashboard.py",
    ]:
        C(f)

    H1("10) المرحلة 6 — استنساخ Edge المحلل Analyst Distillation")
    P("المدة: 3–4 أسابيع ثم تستمر بشكل دائم")
    H2("الهدف")
    P("تحويل خبرة المحلل اليدوي الأفضل لديك إلى features وقواعد ومقاييس قابلة للاختبار.")
    H2("الأعمال المطلوبة")
    for item in [
        "بناء dataset باسم analyst_labels يحتوي timestamp وbias وsetup_type وsweep_side وpoi_type وpoi_quality وintended_entry وtargets وtrade/no-trade.",
        "بناء أداة مقارنة bot vs analyst على نفس الأيام والشارتات.",
        "بناء Analyst Quality Scorer يعتمد على sweep quality وdisplacement strength وPOI freshness وHTF alignment وsession quality.",
        "تغذية dashboard بمقاييس overlap وmissed setups وfalse setups وentry lag وRR difference.",
    ]:
        B(item)
    H2("الملفات المقترحة")
    for f in [
        "services/analyst_distillation.py",
        "scripts/compare_analyst_vs_bot.py",
        "services/database.py",
        "dashboard/ sections جديدة للمقارنة",
    ]:
        C(f)

    H1("11) المرحلة 7 — تحسين الدقة التنفيذية Micro-Precision")
    P("المدة: اختيارية بعد المراحل السابقة")
    H2("الهدف")
    P("رفع الدقة في التنفيذ حتى يقترب النظام من قراءة microstructure البشرية.")
    for item in [
        "إضافة M1/M3 للتنفيذ فقط مع إبقاء التحليل الأعلى على 5m/15m/1H/4H.",
        "إضافة منطق session microstructure مثل London open sweep وNY open sweep وlunch drift وpre-news fake move.",
        "إضافة مصدر بيانات أدق أو fallback أقوى للتنفيذ عندما تسمح البنية بذلك.",
    ]:
        B(item)

    H1("12) الترتيب الزمني المقترح")
    H2("الشهر الأول")
    for item in [
        "المرحلة 0 كاملة",
        "المرحلة 1 كاملة",
        "بداية المرحلة 2",
    ]:
        B(item)
    H2("الشهر الثاني")
    for item in [
        "إكمال المرحلة 2",
        "المرحلة 3",
        "جزء من المرحلة 4",
    ]:
        B(item)
    H2("الشهر الثالث")
    for item in [
        "إكمال المرحلة 4",
        "المرحلة 5",
        "بداية المرحلة 6",
    ]:
        B(item)

    H1("13) الملفات الأعلى أولوية")
    H2("أولوية قصوى")
    for f in [
        "scripts/run_analysis.py",
        "agents/smc_agent.py",
        "agents/decision_agent.py",
        "agents/risk_management_agent.py",
        "agents/open_trades_manager.py",
        "services/database.py",
        "config.json",
    ]:
        C(f)
    H2("أولوية ثانية")
    for f in [
        "services/learning_service.py",
        "services/performance_dashboard.py",
        "services/telegram_bot.py",
        "dashboard/*",
    ]:
        C(f)

    H1("14) أول Sprint مقترح")
    for item in [
        "إضافة setup_type وlead_agent وsetup_quality إلى snapshot الصفقة.",
        "إعادة تفعيل pending/zone execution الحقيقي.",
        "تطوير Telegram message لإظهار zone وinvalidation وtarget liquidity.",
        "إضافة جدول setup_candidates.",
        "توسيع smc_agent لإخراج sweep/displacement/POI structure.",
        "بناء state machine أولي.",
        "فصل 3 strategy profiles.",
        "ربط thresholds بالـ profile.",
        "إضافة تقارير by setup/session/regime.",
        "قياس الفرق بين MARKET وZONE entries.",
    ]:
        B(item)

    H1("15) مؤشرات الأداء الرئيسية KPI")
    for item in [
        "Win rate",
        "Expectancy",
        "Avg R achieved",
        "RR capture %",
        "Entry efficiency",
        "Late entry distance",
        "Missed setup rate",
        "Setup completion rate",
        "Setup invalidation rate",
        "Performance by session",
        "Performance by regime",
        "Performance by setup family",
        "Performance by lead agent",
    ]:
        B(item)

    H1("16) ما الذي لا يجب فعله الآن؟")
    for item in [
        "لا تضف مؤشرات جديدة بكثرة؛ المشكلة الأساسية ليست نقص المؤشرات بل نقص فهم setup lifecycle والتنفيذ من المنطقة.",
        "لا تجعل Gemini صاحب القرار النهائي؛ استخدمه كمراجع أو طبقة دعم فقط.",
        "لا توسّع إلى أصول كثيرة قبل إتقان الذهب.",
        "لا تفعّل auto-learning المباشر قبل وجود baseline واضح وقياسات مستقرة.",
        "لا تعقّد scale-in قبل إصلاح execution وstate machine.",
    ]:
        B(item)

    H1("17) الخلاصة النهائية")
    P("التحول المطلوب ليس مجرد تحسين thresholds داخل Consensus Bot، بل تغيير فلسفة النظام من: مؤشرات + إجماع + فلترة، إلى: Stateful SMC Setup Engine + Strategy Profiles + Zone Execution + Contextual Learning.")
    P("إذا نُفذت هذه الخارطة بالترتيب، فسيقترب النظام كثيرًا من منطق المحلل اليدوي القوي، مع ميزة إضافية لا يملكها معظم المحللين: الانضباط، والقياس، والقدرة على التحسن المستمر.")

    doc.build(story)


def main() -> None:
    md = build_markdown()
    OUT_MD.write_text(md, encoding="utf-8")
    build_pdf()
    print(f"Wrote {OUT_PDF}")
    print(f"Wrote {OUT_MD}")


if __name__ == "__main__":
    main()
