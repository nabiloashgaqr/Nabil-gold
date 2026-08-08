"""Generate the English partnership-consultation invitation PDF.

Approach (operator directive, 2026-08-05): invite the channel owner into the
private signals channel for a limited free period, exactly as it is, with NO
pitch and NO offer. Enter through the door of consulting -- ask his expert
opinion on what the system lacks (targets, stops, liquidity, maps) -- and let
HIM propose a deal after seeing the verified numbers and precision.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, HRFlowable,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "SmartSignal_Consultation_Invite.pdf"

NAVY = colors.HexColor("#0E2A47")
GOLD = colors.HexColor("#B8860B")
GREY = colors.HexColor("#5A6B7C")
LIGHT = colors.HexColor("#F4F7FA")

styles = getSampleStyleSheet()

title = ParagraphStyle("t", parent=styles["Title"], textColor=NAVY,
                       fontSize=22, spaceAfter=4, fontName="Helvetica-Bold")
subtitle = ParagraphStyle("s", parent=styles["Normal"], textColor=GOLD,
                          fontSize=11, spaceAfter=14, fontName="Helvetica-Bold")
h = ParagraphStyle("h", parent=styles["Heading2"], textColor=NAVY,
                   fontSize=13, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold")
body = ParagraphStyle("b", parent=styles["Normal"], fontSize=10.5,
                      leading=16, textColor=colors.black, spaceAfter=6)
quote = ParagraphStyle("q", parent=body, leftIndent=14, rightIndent=14,
                       borderColor=GOLD, borderWidth=0, textColor=GREY,
                       fontName="Helvetica-Oblique", backColor=LIGHT,
                       borderPadding=8, spaceAfter=10, leading=16)
bullet = ParagraphStyle("bl", parent=body, leftIndent=18, bulletIndent=6,
                        spaceAfter=4)
small = ParagraphStyle("sm", parent=body, fontSize=8.5, textColor=GREY, leading=12)

S = []
S.append(Paragraph("SmartSignal — Gold Trading System", title))
S.append(Paragraph("Private Consultation Invitation &amp; Review Questions", subtitle))
S.append(HRFlowable(width="100%", thickness=1.2, color=GOLD, spaceAfter=12))

# --- Honest context
S.append(Paragraph("1 · Who we are (in plain terms)", h))
S.append(Paragraph(
    "SmartSignal is a systematic, fully-documented gold (XAU/USD) trading model that has been "
    "<b>live since 22 June 2026</b> and is developed every day. It is <b>paper-traded only</b> and every "
    "trade is recorded with its entry, exit, and the reason behind each decision. "
    "<b>Errors are expected and are openly documented</b> — we fix them in the open rather than hide them. "
    "We are not selling anything and we are not looking for a deal. We are asking for an experienced eye.",
    body))

# --- Verified stats
S.append(Paragraph("2 · Verified track record (live, auto-updated)", h))
stats = [
    ["Closed trades", "89", "Win rate", "79.5%"],
    ["Net points", "+11,805", "Profit factor", "4.07"],
    ["Best / Worst", "+700 / −400", "Avg trade", "+133"],
    ["Win streak", "16", "RR capture", "43.2% (+1.01R / 2.33R)"],
    ["Best session", "Asia Morning +4,717 pts", "Live page", "smart-signal-lime.vercel.app"],
]
t = Table(stats, hAlign="LEFT", colWidths=[3.1*cm, 4.4*cm, 3.1*cm, 5.4*cm])
t.setStyle(TableStyle([
    ("FONT", (0,0), (-1,-1), "Helvetica", 9.5),
    ("FONT", (1,0), (1,-1), "Helvetica-Bold", 9.5),
    ("FONT", (3,0), (3,-1), "Helvetica-Bold", 9.5),
    ("TEXTCOLOR", (0,0), (-1,-1), GREY),
    ("TEXTCOLOR", (1,0), (1,-1), NAVY),
    ("TEXTCOLOR", (3,0), (3,-1), NAVY),
    ("BACKGROUND", (0,0), (-1,-1), LIGHT),
    ("GRID", (0,0), (-1,-1), 0.4, colors.HexColor("#D7E0E8")),
    ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ("TOPPADDING", (0,0), (-1,-1), 5), ("BOTTOMPADDING", (0,0), (-1,-1), 5),
]))
S.append(t)
S.append(Spacer(1, 6))
S.append(Paragraph("Full live ledger, daily &amp; weekly reports: <b>smart-signal-lime.vercel.app</b>", small))

# --- The invitation
S.append(Paragraph("3 · The invitation (no pitch, no offer)", h))
S.append(Paragraph(
    "Hi — I respect your read of the market and the community you have built. I would value an "
    "experienced eye on my work. I'd like to give you <b>free access to my private signals channel "
    "for two weeks, exactly as it is</b> — nothing will be modified, and the trades are not sent to "
    "anyone else. I am not offering you anything and I will not pitch you. I simply want your honest "
    "opinion: what is missing, and how you see it. If, after seeing the numbers and the precision, "
    "you see a way we could work together, I am open to hearing <b>your</b> idea.",
    quote))

# --- Consulting questions
S.append(Paragraph("4 · Review questions — your expert opinion", h))

S.append(Paragraph("A · Targets (TP1 / TP2)", h))
for q in [
    "Are our first and second targets placed at levels your audience actually respects?",
    "Do we take profit too early or too late relative to how gold really moves in each session?",
    "Which target structure would you trust enough to present to subscribers?",
]:
    S.append(Paragraph(q, bullet, bulletText="•"))

S.append(Paragraph("B · Stop-loss &amp; protection", h))
for q in [
    "Is our stop distance (150 pts floor) sensible for gold's volatility, in your experience?",
    "Does moving the stop to entry at TP1 feel credible, or does it look like over-protection?",
    "What stop behaviour would your audience forgive — and what would they never forgive?",
]:
    S.append(Paragraph(q, bullet, bulletText="•"))

S.append(Paragraph("C · Liquidity", h))
for q in [
    "Do we read liquidity (sweeps, pools) the way your readers expect it to be read?",
    "Is anchoring targets to real liquidity pools a selling point or too technical to show?",
    "What liquidity concept resonates most with an English retail audience?",
]:
    S.append(Paragraph(q, bullet, bulletText="•"))

S.append(Paragraph("D · Day-maps &amp; presentation", h))
for q in [
    "How would you present a daily map to an English audience in one clear line?",
    "What is the first thing you would change about how the system is presented?",
    "What would your subscribers actually pay for here — signals, education, or transparency?",
    "If you ran this, what would you do differently in the first 30 days?",
]:
    S.append(Paragraph(q, bullet, bulletText="•"))

# --- Closing
S.append(Paragraph("5 · A note on honesty", h))
S.append(Paragraph(
    "We publish our wins and our losses alike, and we state plainly that this is paper-trading for "
    "education — not financial advice. We believe that transparency is the only durable edge in this "
    "market. Thank you for your time and your honest read.", body))
S.append(Spacer(1, 8))
S.append(HRFlowable(width="100%", thickness=0.8, color=GOLD, spaceAfter=6))
S.append(Paragraph("SmartSignal · prepared 5 August 2026 · not financial advice", small))

doc = SimpleDocTemplate(str(OUT), pagesize=A4,
                        leftMargin=2*cm, rightMargin=2*cm,
                        topMargin=1.8*cm, bottomMargin=1.8*cm,
                        title="SmartSignal Consultation Invitation")
doc.build(S)
print("PDF written:", OUT)
