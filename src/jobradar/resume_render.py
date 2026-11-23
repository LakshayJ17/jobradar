"""Resume files from profile/resume.yaml data: PDF (to send), Word (to edit) and plain text (to paste).

The PDF is typeset with Typst, bundled with the `typst` Python package, so nothing else needs installing. It uses
New Computer Modern, the font of the LaTeX resume templates most CS students use, in one column with standard
headings and real text. Ligatures are off, so "fi" and "fl" don't turn into single glyphs that some applicant
tracking systems can't read.
"""

from __future__ import annotations

import io
import json
import re
from importlib.resources import files
from pathlib import Path

from .cv import as_text, section_order
from .util import clean_block, clean_line

# heading: "bold" or "smallcaps"; underline: underline links (they are clickable either way).
DEFAULT_STYLE = {"font": "New Computer Modern", "size": 10.0, "paper": "a4", "mx": 1.4, "my": 1.2,
                 "leading": 0.56, "gap": 1.15, "heading": "bold", "name_smallcaps": True, "underline": False}
# Tried in order until the resume fits its page budget: a little tighter, then a little smaller.
SQUEEZE = [{}, {"gap": 0.95, "leading": 0.52}, {"gap": 0.8, "leading": 0.48},
           {"size": 9.8, "gap": 0.7, "leading": 0.46}, {"size": 9.6, "gap": 0.6, "leading": 0.44, "my": 1.0, "mx": 1.25}]
def _s(value) -> str:
    return clean_line(value)


def _url(value) -> str:
    """Links on a resume are web pages, email or phone: nothing else becomes clickable (no javascript:, file:)."""
    url = _s(value)
    return url if re.match(r"(?:https?://|mailto:|tel:)[^\s<>\"]+$", url, re.I) else ""


def _links(items) -> list[dict]:
    return [{"text": _s(x.get("text")), "url": _url(x.get("url"))} for x in items or []
            if isinstance(x, dict) and _s(x.get("text"))]


def contact_lines(cv: dict) -> list[list[dict]]:
    """contact is a list of lines, each a list of {text, url}; a flat list is one line."""
    raw = cv.get("contact") or []
    lines = raw if raw and all(isinstance(x, list) for x in raw) else [raw]
    return [ln for ln in (_links(x) for x in lines) if ln]


def layout(cv: dict) -> list[dict]:
    """The resume as generic blocks both renderers draw: entries (two lines + bullets), skills rows, text, bullets."""
    heads = {"summary": "Summary", "education": "Education", "experience": "Experience", "projects": "Projects",
             "skills": "Technical Skills"} | {k: v for k, v in (cv.get("headings") or {}).items() if v}
    blank = {"l1": "", "r1": "", "l2": "", "r2": "", "l1_links": [], "r1_links": [], "bullets": [], "roles": []}
    out = []
    for key in section_order(cv):
        if key == "summary" and _s(cv.get("summary")):
            out.append({"heading": heads["summary"], "kind": "text", "text": _s(cv["summary"])})
        elif key == "education" and cv.get("education"):
            entries = [blank | {"l1": ", ".join(x for x in (_s(e.get("school")), _s(e.get("place"))) if x),
                                "r1": _s(e.get("dates")), "l2": _s(e.get("degree")), "r2": _s(e.get("score")),
                                "bullets": [_s(x) for x in e.get("details") or [] if _s(x)]}
                       for e in cv["education"]]
            out.append({"heading": heads["education"], "kind": "entries", "entries": entries})
        elif key == "experience" and cv.get("experience"):
            entries = []
            for e in cv["experience"]:
                roles = [r for r in e.get("roles") or [] if _s(r.get("title"))]
                one = len(roles) == 1 and _s(e.get("dates")) in ("", _s(roles[0].get("dates")))
                if one:  # one role: company + dates, then title + place
                    r = roles[0]
                    entries.append(blank | {"l1": _s(e.get("company")), "r1": _s(r.get("dates")),
                                            "l2": _s(r.get("title")), "r2": _s(r.get("place")) or _s(e.get("place")),
                                            "bullets": [_s(b) for b in r.get("bullets") or [] if _s(b)]})
                else:
                    entries.append(blank | {
                        "l1": _s(e.get("company")), "r1": _s(e.get("dates")) or _s(e.get("place")),
                        "roles": [{"l": _s(r.get("title")), "r": _s(r.get("dates")),
                                   "bullets": [_s(b) for b in r.get("bullets") or [] if _s(b)]} for r in roles]})
            out.append({"heading": heads["experience"], "kind": "entries", "entries": entries})
        elif key == "projects" and cv.get("projects"):
            entries = []
            for p in cv["projects"]:
                links = _links(p.get("links"))
                entries.append(blank | {"l1": _s(p.get("name")), "r1": "" if links else _s(p.get("dates")),
                                        "r1_links": links, "l2": _s(p.get("tech")),
                                        "r2": _s(p.get("dates")) if links else "",
                                        "bullets": [_s(b) for b in p.get("bullets") or [] if _s(b)]})
            out.append({"heading": heads["projects"], "kind": "entries", "entries": entries})
        elif key == "skills" and cv.get("skills"):
            rows = [{"label": _s(s.get("label")), "text": ", ".join(_s(i) for i in s.get("items") or [] if _s(i))}
                    for s in cv["skills"]]
            out.append({"heading": heads["skills"], "kind": "skills", "rows": [r for r in rows if r["text"]]})
        else:
            sec = next((s for s in cv.get("sections") or [] if s.get("heading") == key), None)
            if sec and sec.get("bullets"):
                out.append({"heading": _s(sec["heading"]), "kind": "bullets",
                            "bullets": [_s(b) for b in sec["bullets"] if _s(b)]})
    return out


def pdf_bytes(cv: dict, style: dict | None = None) -> bytes:
    import typst

    style = DEFAULT_STYLE | (style or {})
    data = {"name": _s(cv.get("name")) or "Resume", "title": f"{_s(cv.get('name'))} - Resume".strip(" -"),
            "contact": contact_lines(cv), "style": style, "sections": layout(cv)}
    template = files("jobradar").joinpath("templates/resume.typ")
    system_fonts = style["font"] not in ("New Computer Modern", "Libertinus Serif", "DejaVu Sans Mono")
    return typst.compile(str(template), sys_inputs={"data": json.dumps(data, ensure_ascii=False)},
                         ignore_system_fonts=not system_fonts)


def page_count(pdf: bytes) -> int:
    from pypdf import PdfReader

    return len(PdfReader(io.BytesIO(pdf)).pages)


def fit(cv: dict, max_pages: int, style: dict | None = None, cuts: list | None = None) -> tuple[bytes, dict, list]:
    """Render within max_pages: first a slightly smaller, tighter layout, then drop the least relevant bullets
    (cuts: callables that each remove one bullet from cv and return a label, least relevant first).
    Returns (pdf, style used, labels of what was cut)."""
    removed: list[str] = []
    cuts = list(cuts or [])
    while True:
        for step in SQUEEZE:
            used = (style or {}) | step
            pdf = pdf_bytes(cv, used)
            if page_count(pdf) <= max_pages:
                return pdf, used, removed
        if not cuts:
            return pdf, used, removed
        label = cuts.pop(0)(cv)
        if label:
            removed.append(label)


def write_pdf(cv: dict, path: Path, style: dict | None = None) -> Path:
    path.write_bytes(pdf_bytes(cv, style))
    return path


# ---------------------------------------------------------------------------- Word

def write_docx(cv: dict, path: Path, *, font: str = "Calibri", size: float = 10.5, paper: str = "a4") -> Path:
    """An editable copy with the same content and order. Word and Google Docs open it; make your own edits here."""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = (Cm(21.0), Cm(29.7)) if paper == "a4" else (Cm(21.59), Cm(27.94))
    sec.left_margin = sec.right_margin = Cm(1.5)
    sec.top_margin = sec.bottom_margin = Cm(1.3)
    width = sec.page_width - sec.left_margin - sec.right_margin
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = font, Pt(size)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), font)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.line_spacing = 1.0

    def link(par, text: str, url: str) -> None:
        if not url:
            par.add_run(text)
            return
        r_id = par.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        h = OxmlElement("w:hyperlink")
        h.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        for tag, val in (("w:u", "single"), ("w:color", "1F3A93")):
            el = OxmlElement(tag)
            el.set(qn("w:val"), val)
            rpr.append(el)
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        run.append(rpr)
        run.append(t)
        h.append(run)
        par._p.append(h)

    def links(par, items: list[dict]) -> None:
        for i, item in enumerate(items):
            if i:
                par.add_run("  |  ")
            link(par, item["text"], item["url"])

    def pair(left: str, right: str, *, bold=False, italic=False, left_links=(), right_links=()):
        par = doc.add_paragraph()
        par.paragraph_format.tab_stops.add_tab_stop(width, WD_TAB_ALIGNMENT.RIGHT)
        run = par.add_run(left)
        run.bold, run.italic = bold, italic
        if left_links:
            par.add_run("   ")
            links(par, list(left_links))
        par.add_run("\t")
        if right_links:
            links(par, list(right_links))
        else:
            par.add_run(right).italic = italic
        return par

    def bullets(items: list[str]) -> None:
        for b in items:
            par = doc.add_paragraph(b, style="List Bullet")
            par.paragraph_format.left_indent = Cm(0.6)
            par.paragraph_format.space_after = Pt(1)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(_s(cv.get("name")))
    run.bold, run.font.size = True, Pt(size * 1.9)
    for line in contact_lines(cv):
        contact = doc.add_paragraph()
        contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
        links(contact, line)

    for block in layout(cv):
        head = doc.add_paragraph()
        head.paragraph_format.space_before = Pt(7)
        head.paragraph_format.space_after = Pt(3)
        run = head.add_run(block["heading"])
        run.font.small_caps, run.font.size, run.font.color.rgb = True, Pt(size * 1.15), RGBColor(0, 0, 0)
        border = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", "000000")):
            bottom.set(qn(k), v)
        border.append(bottom)
        head._p.get_or_add_pPr().append(border)
        if block["kind"] == "entries":
            for e in block["entries"]:
                pair(e["l1"], e["r1"], bold=True, left_links=e["l1_links"], right_links=e["r1_links"])
                if e["l2"] or e["r2"]:
                    pair(e["l2"], e["r2"], italic=True)
                bullets(e["bullets"])
                for role in e["roles"]:
                    pair(role["l"], role["r"], italic=True)
                    bullets(role["bullets"])
                doc.paragraphs[-1].paragraph_format.space_after = Pt(4)
        elif block["kind"] == "skills":
            for row in block["rows"]:
                par = doc.add_paragraph()
                if row["label"]:
                    par.add_run(row["label"] + ": ").bold = True
                par.add_run(row["text"])
        elif block["kind"] == "text":
            doc.add_paragraph(block["text"])
        else:
            bullets(block["bullets"])
    core = doc.core_properties
    core.author, core.title, core.comments = _s(cv.get("name")), f"{_s(cv.get('name'))} - Resume", ""
    doc.save(str(path))
    return path


def write_text(cv: dict, path: Path) -> Path:
    contact = "\n".join(" | ".join(x["text"] for x in line) for line in contact_lines(cv))
    path.write_text(clean_block(f"{_s(cv.get('name'))}\n{contact}\n\n{as_text(cv)}\n"), encoding="utf-8")
    return path


def file_stem(cv: dict) -> str:
    """Firstname_Lastname_Resume: what recruiters expect to see in their downloads folder."""
    name = re.sub(r"[^A-Za-z0-9]+", "_", _s(cv.get("name"))).strip("_")
    return f"{name}_Resume" if name else "Resume"
