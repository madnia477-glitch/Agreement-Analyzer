"""
Export the Agreement Analysis as a standalone PDF report (spec section 34).
This never touches the original agreement file - it produces a brand new
document that summarizes the analysis with citations and a disclaimer.
"""
from __future__ import annotations
from io import BytesIO
from datetime import date

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)

from .schema import AgreementAnalysis

NAVY = colors.HexColor("#12203B")
BRASS = colors.HexColor("#9C7A3C")
LIGHT = colors.HexColor("#F4F2EC")


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="H1Report", fontSize=20, textColor=NAVY, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="H2Report", fontSize=13, textColor=NAVY, spaceBefore=14, spaceAfter=6, fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="Body", fontSize=9.5, leading=13.5))
    styles.add(ParagraphStyle(name="Small", fontSize=8, textColor=colors.HexColor("#555555")))
    return styles


def _source_str(src) -> str:
    if not src:
        return ""
    return f" (page {src.page})" if src.page else ""


def build_report_pdf(analysis: AgreementAnalysis, filename: str) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=LETTER, topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                             leftMargin=0.75 * inch, rightMargin=0.75 * inch)
    styles = _styles()
    story = []

    story.append(Paragraph("Agreement Review", styles["H1Report"]))
    story.append(Paragraph(f"Source file: {filename} &nbsp;&nbsp;|&nbsp;&nbsp; Generated: {date.today().isoformat()}", styles["Small"]))
    story.append(Spacer(1, 10))

    ov = analysis.document_overview
    overview_rows = [
        ["Agreement Type", ov.agreement_type or "Not determined"],
        ["Institution", ov.institution or "Not determined"],
        ["Product", ov.product or "Not determined"],
        ["Parties", ", ".join(ov.parties) if ov.parties else "Not determined"],
        ["Version", ov.version or "Not determined"],
        ["Effective Date", ov.effective_date or "Not determined"],
        ["Document Length", f"{ov.page_count} pages"],
    ]
    t = Table(overview_rows, colWidths=[1.6 * inch, 4.6 * inch])
    t.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (0, -1), NAVY),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDDDDD")),
    ]))
    story.append(t)

    if analysis.attention_score:
        story.append(Paragraph("Agreement Attention Score", styles["H2Report"]))
        s = analysis.attention_score
        story.append(Paragraph(f"<b>Level: {s.level}</b>", styles["Body"]))
        for f in s.factors:
            story.append(Paragraph(f"&bull; {f}", styles["Body"]))
        story.append(Paragraph(s.disclaimer, styles["Small"]))

    if analysis.executive_summary:
        story.append(Paragraph("Executive Summary", styles["H2Report"]))
        story.append(Paragraph(analysis.executive_summary, styles["Body"]))

    def bullet_section(title, items):
        if not items:
            return
        story.append(Paragraph(title, styles["H2Report"]))
        for item in items:
            story.append(Paragraph(f"&bull; {item}", styles["Body"]))

    def evidence_section(title, items):
        if not items:
            return
        story.append(Paragraph(title, styles["H2Report"]))
        for item in items:
            page_str = f" (page {item.page})" if item.page else ""
            story.append(Paragraph(f"&bull; <b>{item.title}</b>{page_str}: {item.explanation}", styles["Body"]))

    evidence_section("Potential Benefits", analysis.potential_benefits)
    evidence_section("Potential Concerns", analysis.potential_concerns)

    if analysis.important_terms:
        story.append(Paragraph("Important Terms", styles["H2Report"]))
        high = [t for t in analysis.important_terms if t.importance == "high"]
        rows = [["Term", "Category", "Page"]]
        for t in (high or analysis.important_terms)[:25]:
            rows.append([t.term, t.category, str(t.page) if t.page else "-"])
        tbl = Table(rows, colWidths=[2.4 * inch, 2.4 * inch, 1 * inch])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
        ]))
        story.append(tbl)

    if analysis.financial_terms:
        story.append(Paragraph("Financial Obligations, Fees &amp; Charges", styles["H2Report"]))
        rows = [["Item", "Amount / Rule", "Source"]]
        for ftm in analysis.financial_terms:
            rows.append([ftm.label, ftm.amount_or_rule or "Not specified", _source_str(ftm.source) or "-"])
        tbl = Table(rows, colWidths=[1.8 * inch, 3.4 * inch, 1 * inch])
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), LIGHT),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDDDDD")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tbl)

    if analysis.obligations:
        story.append(Paragraph("Your Obligations", styles["H2Report"]))
        for o in analysis.obligations:
            story.append(Paragraph(f"<b>{o.obligation}</b>{_source_str(o.source)}: {o.meaning or ''}", styles["Body"]))

    if analysis.bank_rights:
        story.append(Paragraph("Bank / Institution Rights", styles["H2Report"]))
        for r in analysis.bank_rights:
            story.append(Paragraph(f"<b>{r.right}</b>{_source_str(r.source)}: {r.conditions or ''}", styles["Body"]))

    if analysis.default_clauses:
        story.append(Paragraph("What Can Put You in Default", styles["H2Report"]))
        for d in analysis.default_clauses:
            story.append(Paragraph(f"<b>{d.trigger}</b>{_source_str(d.source)}: {d.stated_consequence or 'Consequence not stated'}", styles["Body"]))

    if analysis.termination_clauses:
        story.append(Paragraph("How This Agreement Can End", styles["H2Report"]))
        for tcl in analysis.termination_clauses:
            desc = f"Who: {tcl.who_can_terminate or '-'} | When: {tcl.when or '-'} | Notice: {tcl.notice_required or '-'} | Fee: {tcl.fee or '-'}"
            story.append(Paragraph(desc + _source_str(tcl.source), styles["Body"]))

    if analysis.early_settlement and analysis.early_settlement.present:
        story.append(Paragraph("Early Settlement", styles["H2Report"]))
        es = analysis.early_settlement
        story.append(Paragraph(f"Conditions: {es.conditions or 'Not specified'}", styles["Body"]))
        story.append(Paragraph(f"Charges: {es.charges or 'Not specified'}", styles["Body"]))
        story.append(Paragraph(f"Notice requirement: {es.notice_requirement or 'Not specified'}", styles["Body"]))

    if analysis.attention_areas:
        story.append(Paragraph("Attention Areas", styles["H2Report"]))
        for a in analysis.attention_areas:
            story.append(Paragraph(f"<b>[{a.level}] {a.topic}</b>{_source_str(a.source)}: {a.what_document_says}", styles["Body"]))

    if analysis.signatures:
        story.append(Paragraph("Signature &amp; Consent Requirements", styles["H2Report"]))
        for s in analysis.signatures:
            blank = " (appears blank)" if s.appears_blank else ""
            story.append(Paragraph(f"<b>{s.field}</b> - page {s.page}{blank}: {s.meaning or ''}", styles["Body"]))

    if analysis.missing_fields:
        story.append(Paragraph("Information That May Need Completion", styles["H2Report"]))
        for m in analysis.missing_fields:
            story.append(Paragraph(f"&bull; {m.field} (page {m.page})", styles["Body"]))

    if analysis.questions_to_consider:
        story.append(Paragraph("Questions to Consider Before Signing", styles["H2Report"]))
        for q in analysis.questions_to_consider:
            story.append(Paragraph(f"&bull; {q}", styles["Body"]))

    if analysis.before_you_sign_checklist:
        story.append(Paragraph("Before You Sign", styles["H2Report"]))
        for c in analysis.before_you_sign_checklist:
            story.append(Paragraph(f"&#9744; {c}", styles["Body"]))

    story.append(Spacer(1, 16))
    story.append(Paragraph(
        "This report is generated by an Agreement Understanding &amp; Review Assistant. It is not legal "
        "or financial advice, and it does not state whether this agreement is safe or advisable to sign. "
        "For high-stakes decisions, consider having the original agreement reviewed by a qualified "
        "professional.", styles["Small"],
    ))

    doc.build(story)
    return buf.getvalue()
