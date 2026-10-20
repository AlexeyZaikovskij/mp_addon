"""PDF export of both reports (ReportLab; charts and the timeline are drawn with its vector graphics).

A4 landscape. The fonts are bundled (DejaVu Sans) because the built-in PDF fonts have no Cyrillic.
"""
import io
import math
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, PolyLine, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (
    KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from ..config import ROOT
from . import timeline
from .export import fmt_dt, fmt_duration, fmt_num, status_text

# --- Fonts, colours, styles -------------------------------------------------------

_FONTS = ROOT / "app" / "fonts"
pdfmetrics.registerFont(TTFont("DejaVu", str(_FONTS / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(_FONTS / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFontFamily("DejaVu", normal="DejaVu", bold="DejaVu-Bold", italic="DejaVu", boldItalic="DejaVu-Bold")

INK = colors.HexColor("#1d2433")
MUTED = colors.HexColor("#6b7486")
BORDER = colors.HexColor("#dde2ea")
HEAD_BG = colors.HexColor("#f2f4f7")
GRID = colors.HexColor("#e7ebf1")
TRACK = colors.HexColor("#eef1f5")
OUTAGE = colors.HexColor("#d03b3b")
DEGRADED = colors.HexColor("#fab219")
BAD = colors.HexColor("#a12626")
PALETTE = [colors.HexColor(c) for c in
           ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")]
PRIORITY_BG = {
    "error": colors.HexColor("#fde2e2"), "warning": colors.HexColor("#fff0d9"),
    "caution": colors.HexColor("#fdf6c9"), "advisory": colors.HexColor("#e2edff"), "log": colors.HexColor("#eceff3"),
}

PAGE = landscape(A4)
MARGIN = 12 * mm
WIDTH = PAGE[0] - 2 * MARGIN

S_TITLE = ParagraphStyle("title", fontName="DejaVu-Bold", fontSize=16, leading=20, textColor=INK, spaceAfter=4)
S_H2 = ParagraphStyle("h2", fontName="DejaVu-Bold", fontSize=11, leading=14, textColor=INK, spaceBefore=10, spaceAfter=5)
S_BODY = ParagraphStyle("body", fontName="DejaVu", fontSize=8.5, leading=11, textColor=INK)
S_CELL = ParagraphStyle("cell", fontName="DejaVu", fontSize=7.5, leading=9.2, textColor=INK)
S_CELL_B = ParagraphStyle("cellb", parent=S_CELL, fontName="DejaVu-Bold")
S_CELL_R = ParagraphStyle("cellr", parent=S_CELL, alignment=TA_RIGHT)
S_CELL_BR = ParagraphStyle("cellbr", parent=S_CELL_B, alignment=TA_RIGHT)
S_NOTE = ParagraphStyle("note", fontName="DejaVu", fontSize=7.5, leading=9.5, textColor=MUTED, spaceBefore=4)
S_LABEL = ParagraphStyle("label", fontName="DejaVu", fontSize=7, leading=9, textColor=MUTED)
S_VALUE = ParagraphStyle("value", fontName="DejaVu-Bold", fontSize=13, leading=16, textColor=INK)


def P(text, style=S_CELL) -> Paragraph:
    return Paragraph(escape(str(text if text is not None else "")), style)


def _table(data, widths, header_rows=1, extra=()) -> Table:
    tbl = Table(data, colWidths=widths, repeatRows=header_rows)
    tbl.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "DejaVu", 7.5),
        ("FONT", (0, 0), (-1, header_rows - 1), "DejaVu-Bold", 7.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("BACKGROUND", (0, 0), (-1, header_rows - 1), HEAD_BG),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        *extra,
    ]))
    return tbl


# --- Page frame: footer with "page N of M" -----------------------------------------------

def _numbered_canvas(footer: str, page_label: str):
    class NumberedCanvas(rl_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved = []

        def showPage(self):
            self._saved.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved)
            for state in self._saved:
                self.__dict__.update(state)
                self.setFont("DejaVu", 7)
                self.setFillColor(MUTED)
                self.drawString(MARGIN, 7 * mm, footer)
                self.drawRightString(PAGE[0] - MARGIN, 7 * mm,
                                     page_label.replace("{n}", str(self._pageNumber)).replace("{total}", str(total)))
                super().showPage()
            super().save()

    return NumberedCanvas


def _document(story, title: str, footer: str, t) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN + 4 * mm,
        title=title, author="MultiProbe Reports",
    )
    doc.build(story, canvasmaker=_numbered_canvas(footer, t("pdf_page")))
    return buf.getvalue()


def _header(title: str, meta: list[tuple[str, str]]) -> list:
    cells = [[P(label, S_LABEL) for label, _ in meta], [P(value, S_CELL_B) for _, value in meta]]
    # The period is the long value: give it twice the room so it stays on one line.
    weights = [2 if len(value) > 30 else 1 for _, value in meta]
    unit = WIDTH / sum(weights)
    tbl = Table(cells, colWidths=[unit * w for w in weights], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 16),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
    ]))
    return [Paragraph(escape(title), S_TITLE), tbl, Spacer(1, 8)]


def _tiles(items: list[tuple[str, str]]) -> Table:
    cells = [[[P(label, S_LABEL), P(value, S_VALUE)] for label, value in items]]
    tbl = Table(cells, colWidths=[WIDTH / len(items)] * len(items), hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER), ("INNERGRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return tbl


def _fit(text: str, max_width: float, font: str, size: float) -> str:
    if pdfmetrics.stringWidth(text, font, size) <= max_width:
        return text
    while text and pdfmetrics.stringWidth(text + "…", font, size) > max_width:
        text = text[:-1]
    return text + "…"


# --- Problem timeline ---------------------------------------------------------------

TL_LABEL_W = 190
TL_ROW_H = 11
TL_ROWS_PER_BLOCK = 12  # small blocks so the timeline starts right under the summary and breaks across pages


def timeline_drawing(rows: list, ticks: list, width: float = WIDTH) -> Drawing:
    plot_w = width - TL_LABEL_W
    height = 14 + len(rows) * TL_ROW_H
    d = Drawing(width, height)
    top = height - 14
    for tick in ticks:
        x = TL_LABEL_W + plot_w * tick.pct / 100
        d.add(Line(x, 0, x, top, strokeColor=GRID, strokeWidth=0.4))
        d.add(String(x + 1.5, top + 4, tick.label, fontName="DejaVu", fontSize=6, fillColor=MUTED))
    for i, tr in enumerate(rows):
        y = top - (i + 1) * TL_ROW_H
        indent = 8 * min(tr.row.depth, 3)
        font = "DejaVu-Bold" if tr.row.depth == 0 else "DejaVu"
        d.add(String(indent, y + 3, _fit(tr.row.name, TL_LABEL_W - indent - 6, font, 6.5),
                     fontName=font, fontSize=6.5, fillColor=INK))
        d.add(Rect(TL_LABEL_W, y + 1.5, plot_w, TL_ROW_H - 3, fillColor=TRACK, strokeColor=None))
        for b in tr.bars:
            d.add(Rect(TL_LABEL_W + plot_w * b.left / 100, y + 2.5, max(plot_w * b.width / 100, 0.8), TL_ROW_H - 5,
                       fillColor=OUTAGE if b.level == "outage" else DEGRADED, strokeColor=None))
    return d


def _legend(items: list[tuple[colors.Color, str]]) -> Drawing:
    d = Drawing(WIDTH, 12)
    x = 0
    for color, label in items:
        d.add(Rect(x, 2, 8, 8, fillColor=color, strokeColor=None))
        d.add(String(x + 11, 3, label, fontName="DejaVu", fontSize=7, fillColor=INK))
        x += 11 + pdfmetrics.stringWidth(label, "DejaVu", 7) + 14
        if x > WIDTH - 60:
            break
    return d


# --- Line chart --------------------------------------------------------------------------

def _nice_ticks(lo: float, hi: float, count: int = 5) -> list[float]:
    if lo == hi:
        pad = abs(lo) * 0.1 or 1
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, v = [], start
    while v <= hi + step * 0.001:
        ticks.append(round(v, 10))
        v += step
    if ticks[-1] < hi:
        ticks.append(ticks[-1] + step)
    return ticks


def _segments(points):
    """Split [(x, y)] at missing values so gaps stay gaps."""
    seg = []
    for x, y in points:
        if y is None:
            if seg:
                yield seg
            seg = []
        else:
            seg.append((x, y))
    if seg:
        yield seg


def line_chart(series: list[dict], x0: datetime, x1: datetime, width: float = WIDTH, height: float = 160) -> Drawing:
    """series: {color, points: [(datetime, y|None)], band: [(datetime, lo|None, hi|None)] | None}"""
    left, right, top, bottom = 46, 6, 6, 22
    pw, ph = width - left - right, height - top - bottom
    d = Drawing(width, height)
    values = [y for s in series for _, y in s["points"] if y is not None]
    values += [v for s in series for _, lo, hi in (s.get("band") or []) for v in (lo, hi) if v is not None]
    if not values:
        d.add(String(left, height / 2, "—", fontName="DejaVu", fontSize=8, fillColor=MUTED))
        return d
    yt = _nice_ticks(min(values), max(values))
    ylo, yhi = yt[0], yt[-1]
    span = (x1 - x0).total_seconds() or 1

    def X(t: datetime) -> float:
        return left + pw * (t - x0).total_seconds() / span

    def Y(v: float) -> float:
        return bottom + ph * (v - ylo) / ((yhi - ylo) or 1)

    for v in yt:
        d.add(Line(left, Y(v), left + pw, Y(v), strokeColor=GRID, strokeWidth=0.4))
        d.add(String(left - 4, Y(v) - 2.5, fmt_num(v, False, 2), fontName="DejaVu", fontSize=6,
                     fillColor=MUTED, textAnchor="end"))
    for tick in timeline._ticks(x0, x1):
        x = left + pw * tick.pct / 100
        d.add(Line(x, bottom, x, bottom + ph, strokeColor=GRID, strokeWidth=0.4))
        d.add(String(x, bottom - 9, tick.label, fontName="DejaVu", fontSize=6, fillColor=MUTED, textAnchor="middle"))

    for s in series:
        band = s.get("band")
        if band:
            for seg in _segments([(t, (lo, hi) if lo is not None and hi is not None else None) for t, lo, hi in band]):
                pts = [(X(t), Y(lo)) for t, (lo, _) in seg] + [(X(t), Y(hi)) for t, (_, hi) in reversed(seg)]
                d.add(Polygon([c for p in pts for c in p], fillColor=s["color"], fillOpacity=0.15, strokeColor=None))
    for s in series:
        pts = s["points"]
        stride = max(1, len(pts) // 1500)  # plenty for a page width
        for seg in _segments(pts[::stride]):
            if len(seg) == 1:
                x, y = seg[0]
                d.add(Rect(X(x) - 0.8, Y(y) - 0.8, 1.6, 1.6, fillColor=s["color"], strokeColor=None))
            else:
                d.add(PolyLine([c for x, y in seg for c in (X(x), Y(y))], strokeColor=s["color"], strokeWidth=1.1))
    return d


# --- Alarm history report ------------------------------------------------------------------

def _common_meta(job, report, t, period: list[str], generated: str) -> list[tuple[str, str]]:
    p = job.params
    meta = []
    if p.get("template"):
        meta.append((t("template"), p["template"]))
    meta += [
        (t("server"), p.get("server", "")),
        (t("period"), f"{period[0]} — {period[1]}"),
        (t("objects"), str(len(p.get("object_ids") or []))),
        (t("user"), p.get("user", "")),
        (t("time_zone"), p.get("time_zone", "")),
        (t("generated_at"), generated),
    ]
    return meta


def alarms_pdf(job, report, t, period: list[str], generated: str, max_rows: int, sections: set[str]) -> bytes:
    s = report.summary
    title = t("report_alarms_title")
    story = _header(title, _common_meta(job, report, t, period, generated))

    worst = f"{s['worst'][0]} ({fmt_duration(s['worst'][1])})" if s.get("worst") else "—"
    if "summary" in sections:
        story += _alarm_summary(s, t, worst)
    if "timeline" in sections:
        story += _alarm_timeline(report, t)
    if "availability" in sections:
        story += _availability(report, t)
    if "top" in sections and s["total"]:
        story += _tops(s, t)
    if "list" in sections:
        story += _alarm_list(report, t, max_rows)
    footer = f"MultiProbe Reports · {title} · {period[0]} — {period[1]}"
    return _document(story, title, footer, t)


def _alarm_summary(s, t, worst) -> list:
    story = [_tiles([
        (t("sum_total"), str(s["total"])),
        (t("sum_active"), str(s["active"])),
        (t("sum_affected"), f"{s['objects_with_outage']} / {s['objects']}"),
        (t("sum_availability"), f"{s['availability']:.2f} %"),
        (t("sum_worst"), worst),
    ])]
    if s["by_group"]:
        story.append(Paragraph(escape("   ".join(f"{name}: {count}" for name, count in s["by_group"])), S_NOTE))
    return story


def _alarm_timeline(report, t) -> list:
    story = []
    # Timeline: objects with problems only, split into blocks so it can break across pages.
    tl_end = min(report.period[1], report.generated.astimezone(report.period[0].tzinfo))
    ticks, tl_rows = timeline.build(report.availability, report.period[0], tl_end, t)
    problem_rows = [r for r in tl_rows if r.shown_when_filtered]
    story.append(Paragraph(escape(t("timeline")), S_H2))
    if problem_rows:
        story.append(_legend([(OUTAGE, f"{t('tl_outage')} ({t('tl_outage_hint')})"),
                              (DEGRADED, f"{t('tl_degraded')} ({t('tl_degraded_hint')})")]))
        for i in range(0, len(problem_rows), TL_ROWS_PER_BLOCK):
            story.append(timeline_drawing(problem_rows[i:i + TL_ROWS_PER_BLOCK], ticks))
    else:
        story.append(P(t("no_problems"), S_BODY))
    return story


def _availability(report, t) -> list:
    story = []
    story.append(Paragraph(escape(t("availability_title")), S_H2))
    rows = [[P(t("col_object"), S_CELL_B)] + [P(t(k), S_CELL_BR) for k in
             ("tl_outage", "tl_degraded", "col_availability", "col_incidents", "col_longest")]]
    extra = []
    for i, a in enumerate(report.availability, start=1):
        name = Paragraph(("&nbsp;" * 4 * min(a.depth, 3)) + escape(a.name)
                         + f' <font color="#6b7486" size="6.5">{escape(a.kind_name)}</font>',
                         S_CELL_B if a.depth == 0 else S_CELL)
        rows.append([name, fmt_duration(a.outage_sec), fmt_duration(a.degraded_sec),
                     f"{a.availability:.2f} %", str(a.incidents), fmt_duration(a.longest_sec)])
        if a.outage_sec:
            extra += [("TEXTCOLOR", (1, i), (1, i), BAD), ("TEXTCOLOR", (3, i), (3, i), BAD)]
    w = WIDTH
    story.append(_table(rows, [w * 0.42, w * 0.11, w * 0.11, w * 0.12, w * 0.1, w * 0.14],
                        extra=[("ALIGN", (1, 0), (-1, -1), "RIGHT"), *extra]))
    story.append(Paragraph(escape(t("availability_note")), S_NOTE))
    return story


def _tops(s, t) -> list:
    """Most frequent alarms and objects, side by side."""
    def top_table(head, items):
        data = [[P(head, S_CELL_B), ""]] + [[P(name), str(count)] for name, count in items]
        return _table(data, [WIDTH * 0.39, WIDTH * 0.08], extra=[("ALIGN", (1, 0), (1, -1), "RIGHT")])

    pair = Table([[top_table(t("top_alarms"), s["top_alarms"]), top_table(t("top_objects"), s["top_objects"])]],
                 colWidths=[WIDTH / 2, WIDTH / 2], hAlign="LEFT")
    pair.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    return [Spacer(1, 10), pair]


def _alarm_list(report, t, max_rows: int) -> list:
    """Newest first, capped — the full list is always in CSV/XML. The cap is announced up front."""
    story = [Paragraph(escape(t("pdf_alarm_list")), S_H2)]
    shown = report.rows[:max_rows]
    if len(report.rows) > len(shown):
        story.append(Paragraph(escape(t("pdf_rows_limited").replace("{n}", str(len(shown)))
                                      .replace("{total}", str(len(report.rows)))), S_NOTE))
        story.append(Spacer(1, 4))
    head = [t("col_group"), t("col_alarm"), t("col_condition"), t("col_stream"), t("col_object"),
            t("col_detected"), t("col_finished"), t("col_duration")]
    data = [[P(h, S_CELL_BR if h == head[-1] else S_CELL_B) for h in head]]
    extra = []
    for i, r in enumerate(shown, start=1):
        data.append([
            P(f"{r.group} {r.priority}"), P(r.alarm), P(r.condition), P(r.stream), P(r.object),
            fmt_dt(r.detected), status_text(r, t) if r.active else fmt_dt(r.finished), fmt_duration(r.duration_sec),
        ])
        bg = PRIORITY_BG.get(r.group.lower())
        if bg:
            extra.append(("BACKGROUND", (0, i), (0, i), bg))
        if r.active:
            extra.append(("TEXTCOLOR", (6, i), (6, i), BAD))
    w = WIDTH
    story.append(_table(data, [w * 0.08, w * 0.17, w * 0.13, w * 0.08, w * 0.2, w * 0.12, w * 0.12, w * 0.10],
                        extra=[("ALIGN", (7, 0), (7, -1), "RIGHT"), *extra]))
    return story


# --- Measurement report ------------------------------------------------------------------

def measures_pdf(job, report, t, period: list[str], generated: str, max_series_per_chart: int,
                 sections: set[str]) -> bytes:
    title = t("report_measures_title")
    meta = _common_meta(job, report, t, period, generated)
    meta.insert(-2, (t("resolution"), t("res_" + report.resolution)))
    story = _header(title, meta)

    if not report.series:
        story.append(P(t("no_measure_data"), S_BODY))
        return _document(story, title, f"MultiProbe Reports · {title}", t)

    x0, x1 = report.period
    if "charts" in sections:
        for key, mtitle, unit in report.measures():
            group = [s for s in report.series if s.key == key]
            shown = group[:max_series_per_chart]
            single = len(shown) == 1 and report.resolution != "raw"
            chart_series = [{
                "color": PALETTE[i],
                "points": [(p.time, s.d(p.avg)) for p in s.points],
                "band": [(p.time, s.d(p.min), s.d(p.max)) for p in s.points] if single else None,
            } for i, s in enumerate(shown)]
            head = f"{mtitle}" + (f" ({unit})" if unit else "") + f" · {group[0].analyzer}"
            block = [Paragraph(escape(head), S_H2), line_chart(chart_series, x0, x1)]
            block.append(_legend([(PALETTE[i], s.object) for i, s in enumerate(shown)]))
            notes = []
            if single:
                notes.append(t("chart_band_hint"))
            if len(group) > len(shown):
                notes.append(t("chart_limited").replace("{n}", str(len(shown))).replace("{total}", str(len(group))))
            if notes:
                block.append(Paragraph(escape(" ".join(notes)), S_NOTE))
            story.append(KeepTogether(block))

    if "summary" in sections:
        story += _measure_summary(report, t)
    if "values" in sections:
        story.append(Paragraph(escape(t("pdf_values_in_csv")), S_NOTE))

    footer = f"MultiProbe Reports · {title} · {period[0]} — {period[1]}"
    return _document(story, title, footer, t)


def _measure_summary(report, t) -> list:
    story = [Paragraph(escape(t("measure_summary")), S_H2)]
    head = [t("col_stream"), t("col_object"), t("col_min"), t("col_avg"), t("col_max"), t("col_std"), t("col_coverage")]
    data = [[P(h, S_CELL_B if i < 2 else S_CELL_BR) for i, h in enumerate(head)]]
    extra = []
    for key, mtitle, unit in report.measures():
        group = [s for s in report.series if s.key == key]
        i = len(data)
        data.append([Paragraph(f"<b>{escape(mtitle)}</b>" + (f' <font color="#6b7486">{escape(unit)}</font>' if unit else "")
                               + f' <font color="#6b7486">· {escape(group[0].analyzer)}</font>', S_CELL), "", "", "", "", "", ""])
        extra += [("SPAN", (0, i), (-1, i)), ("BACKGROUND", (0, i), (-1, i), HEAD_BG)]
        for s in group:
            st = s.stats
            cov = st.get("coverage")
            data.append([P(s.stream), P(s.object)] + [fmt_num(s.d(st.get(k)), False, 2) for k in ("min", "avg", "max", "std")]
                        + [f"{cov:.0f} %" if cov is not None else str(st.get("points", 0))])
            if cov is not None and cov < 90:
                extra.append(("TEXTCOLOR", (6, len(data) - 1), (6, len(data) - 1), BAD))
    w = WIDTH
    story.append(_table(data, [w * 0.14, w * 0.3, w * 0.11, w * 0.11, w * 0.11, w * 0.11, w * 0.12],
                        extra=[("ALIGN", (2, 0), (-1, -1), "RIGHT"), *extra]))
    story.append(Paragraph(escape(t("measure_note")), S_NOTE))
    return story
