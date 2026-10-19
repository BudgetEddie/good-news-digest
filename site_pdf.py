"""The day's stories as a printable PDF (public/today.pdf), linked from the front page."""
from __future__ import annotations

import html
import unicodedata
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer

INK = colors.HexColor("#1d2622")
MUTED = colors.HexColor("#5b6962")
LINK = colors.HexColor("#1c6a50")
ACCENT = colors.HexColor("#e8890c")
LINE = colors.HexColor("#ecdfc8")

STYLES = {
    "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=30, leading=34, textColor=INK),
    "dateline": ParagraphStyle("dateline", fontName="Helvetica", fontSize=11, leading=15, textColor=MUTED),
    "section": ParagraphStyle(
        "section", fontName="Helvetica-Bold", fontSize=10, leading=14, textColor=ACCENT, spaceBefore=14, spaceAfter=2
    ),
    "headline": ParagraphStyle("headline", fontName="Times-Bold", fontSize=14, leading=17.5, textColor=INK, spaceBefore=8),
    "summary": ParagraphStyle("summary", fontName="Helvetica", fontSize=10.5, leading=14.5, textColor=INK, spaceBefore=3),
    "source": ParagraphStyle("source", fontName="Helvetica", fontSize=9, leading=13, textColor=MUTED, spaceBefore=2),
    "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=8.5, leading=12, textColor=MUTED),
}


def pdf_text(text: str) -> str:
    """Escaped for ReportLab markup and limited to what its built-in fonts can draw (Windows-1252).

    Accented letters, curly quotes and dashes survive; anything else is reduced to its plain
    letters (ﬁ -> fi) or dropped, so an emoji in a headline never prints as a black box.
    """
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            out.append(unicodedata.normalize("NFKD", ch).encode("cp1252", "ignore").decode("cp1252"))
    return html.escape(" ".join("".join(out).split()), quote=True)  # a dropped emoji leaves no double space


def _link(url: str, text: str, color: str = "#1c6a50", underline: bool = True) -> str:
    """text must already be escaped with pdf_text."""
    inner = f"<u>{text}</u>" if underline else text
    return f'<a href="{pdf_text(url)}" color="{color}">{inner}</a>'


def build_pdf(
    path: Path,
    stories: list[dict],
    day: datetime,
    categories: dict[str, tuple[str, str]],
    site_name: str,
    site_url: str,
) -> None:
    """stories: one day's stories; categories: display order, key -> (label, icon)."""
    story_word = "story" if len(stories) == 1 else "stories"
    flow = [
        Paragraph(pdf_text(site_name), STYLES["title"]),
        Spacer(0, 4),
        Paragraph(pdf_text(f"{day:%A}, {day:%B} {day.day}, {day:%Y} · {len(stories)} good news {story_word}"), STYLES["dateline"]),
        Spacer(0, 8),
        HRFlowable(width="100%", thickness=2, color=ACCENT, spaceAfter=4),
    ]
    for key, (label, _icon) in categories.items():
        in_section = [s for s in stories if s["category"] == key]
        if not in_section:
            continue
        heading = Paragraph(pdf_text(label.upper()), STYLES["section"])
        for n, s in enumerate(sorted(in_section, key=lambda s: (s["score"], s["published"]), reverse=True)):
            parts = [heading] if n == 0 else []  # a heading never sits alone at the bottom of a page
            parts += [Paragraph(_link(s["url"], pdf_text(s["title"]), color="#1d2622", underline=False), STYLES["headline"])]
            if s.get("summary"):
                parts.append(Paragraph(pdf_text(s["summary"]), STYLES["summary"]))
            parts.append(Paragraph(f"{pdf_text(s['source'])} · {_link(s['url'], 'Read the full story')}", STYLES["source"]))
            flow.append(KeepTogether(parts))  # never split a story across pages
        flow.append(Spacer(0, 4))

    home = _link(site_url, pdf_text(site_url.removeprefix("https://"))) if site_url else pdf_text(site_name)
    flow += [
        Spacer(0, 14),
        HRFlowable(width="100%", thickness=0.75, color=LINE, spaceAfter=6),
        Paragraph(
            f"From {home}. Headlines and articles belong to their publishers. "
            "Summaries are written by AI from the publisher's own snippet and can contain mistakes.",
            STYLES["footer"],
        ),
    ]

    def page_number(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8.5)
        canvas.setFillColor(MUTED)
        canvas.drawRightString(letter[0] - 0.75 * inch, 0.5 * inch, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        str(path),
        pagesize=letter,
        leftMargin=0.85 * inch,
        rightMargin=0.85 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.8 * inch,
        title=f"{site_name}, {day:%B} {day.day}, {day:%Y}",
        author=site_name,
    )
    doc.build(flow, onFirstPage=page_number, onLaterPages=page_number)
