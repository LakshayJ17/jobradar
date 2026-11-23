"""Spreadsheet export: data/jobs.xlsx, written every run and attached to the email / Telegram digest.

An .xlsx opens on any device (Excel, Google Sheets, Numbers, LibreOffice, the Gmail and Outlook phone apps), keeps
clickable links and needs no internet. Sheets: open matches, new today, in progress, and everything tracked.
"""

from __future__ import annotations

from pathlib import Path

from .report import posted_label, reviews_url, why
from .salary import in_units, unit
from .store import Store, status_label
from .util import safe_url, today

CELL_LIMIT = 32_000   # Excel refuses cells over 32,767 characters
LINK_LIMIT = 2_000    # and hyperlinks over 2,079

ACTIVE = ("saved", "applied", "oa", "interview", "offer")
COLUMNS = [  # (header, width)
    ("ID", 6), ("Score", 7), ("AI verdict", 10), ("Company", 24), ("Role", 44), ("Location", 30), ("Preferred", 10),
    ("Pay min", 9), ("Pay max", 9), ("Pay unit", 9), ("Posted", 11), ("Age", 8), ("Status", 11), ("Why / gaps", 70),
    ("Job link", 40), ("Reviews", 12), ("Notes", 30),
]
_VERDICT_COLOURS = {"apply": "1F7A4D", "maybe": "9A6A00", "skip": "AA3333", "no": "AA3333"}


def _row(rec: dict, store: Store) -> list:
    ai = rec.get("ai") or {}
    pay = in_units(rec.get("salary"))
    verdict = "ineligible" if ai.get("eligible") == "no" else ai.get("verdict", "")
    status = status_label(rec) + (f" (closed: {rec['closed_reason']})" if rec.get("closed") and rec.get("closed_reason")
                                  else " (closed)" if rec.get("closed") else "")
    return [rec["id"], store.score_of(rec), verdict, rec["company"], rec["title"], rec.get("location") or "",
            "yes" if rec.get("rank_bonus") else "", round(pay[0], 1) if pay else None,
            round(pay[1], 1) if pay else None, unit() if pay else "", rec.get("posted_at") or "",
            posted_label(rec), status, why(rec), rec["url"], "reviews", rec.get("notes") or ""]


def write_xlsx(store: Store, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    today_iso = today().isoformat()
    everything = store.ranked()
    visible = [r for r in everything if store.visible(r)]
    sheets = [
        ("Open matches", visible),
        ("New today", [r for r in visible if r.get("first_seen") == today_iso]),
        ("In progress", [r for r in everything if r.get("status") in ACTIVE]),
        ("All tracked", everything),
    ]
    wb = Workbook()
    wb.remove(wb.active)
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="2F5BD3")
    link_font = Font(color="2F5BD3", underline="single")
    wrap = Alignment(wrap_text=True, vertical="top")
    top = Alignment(vertical="top")
    for title, rows in sheets:
        ws = wb.create_sheet(title)
        ws.append([h for h, _ in COLUMNS])
        for col, (_, width) in enumerate(COLUMNS, 1):
            cell = ws.cell(1, col)
            cell.font, cell.fill = head_font, head_fill
            ws.column_dimensions[get_column_letter(col)].width = width
        for rec in rows:
            values = _row(rec, store)
            r = ws.max_row + 1
            for col, value in enumerate(values, 1):
                if isinstance(value, str):
                    value = ILLEGAL_CHARACTERS_RE.sub("", value)[:CELL_LIMIT]
                cell = ws.cell(r, col, value)
                if isinstance(value, str):
                    cell.data_type = "s"  # job text is never a formula, even when it starts with "="
                cell.alignment = wrap if COLUMNS[col - 1][0] in ("Why / gaps", "Role", "Location") else top
            for col, url in ((15, safe_url(rec["url"])), (16, safe_url(reviews_url(rec["company"])))):
                if url and len(url) <= LINK_LIMIT and not ILLEGAL_CHARACTERS_RE.search(url):
                    ws.cell(r, col).hyperlink = url  # only plain http(s) links become clickable
                    ws.cell(r, col).font = link_font
            colour = _VERDICT_COLOURS.get("no" if values[2] == "ineligible" else values[2])
            if colour:
                ws.cell(r, 3).font = Font(bold=True, color=colour)
        ws.freeze_panes = "E2"  # header row and ID..Company stay put while scrolling
        if ws.max_row > 1:
            ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{ws.max_row}"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def summary_line(store: Store) -> str:
    today_iso = today().isoformat()
    visible = [r for r in store.all() if store.visible(r)]
    new = sum(1 for r in visible if r.get("first_seen") == today_iso)
    active = sum(1 for r in store.all() if r.get("status") in ACTIVE)
    return f"{len(visible)} open matches, {new} new today, {active} in progress"
