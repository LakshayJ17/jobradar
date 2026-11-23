"""Your resume as structured data: profile/resume.yaml.

Tailoring a resume needs to know what is a project, a bullet, a skill. `jobradar resume --setup` reads your resume
file once and writes profile/resume.yaml:

* The AI only sorts your text into sections. It is told to copy every line exactly, and each bullet is then checked
  against the original file. Anything that doesn't match is listed so you can fix it.
* Your name and contact line never go to the AI. They are read from the top of your resume on this machine.
* Links behind words like "Live | GitHub" are read from the PDF itself.

The YAML is yours to edit, and tailoring only reorders, trims and lightly rewords what is in it.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .privacy import _EMAIL, _HEADING, _NAME_LINE, _NOT_A_NAME, _PERSONAL_LINE, Redacted

CV_FILE = "resume.yaml"
BUILTIN = ("summary", "education", "experience", "projects", "skills")

_STR = {"type": "STRING"}
_LIST = {"type": "ARRAY", "items": _STR}


def _obj(**props) -> dict:
    """Every field required: Gemini leaves optional fields out even when the resume has them."""
    return {"type": "OBJECT", "properties": props, "required": list(props), "propertyOrdering": list(props)}


CV_SCHEMA = _obj(
    summary=_STR,
    education={"type": "ARRAY", "items": _obj(school=_STR, place=_STR, degree=_STR, dates=_STR, score=_STR,
                                              details=_LIST)},
    experience={"type": "ARRAY", "items": _obj(
        company=_STR, place=_STR, dates=_STR,
        roles={"type": "ARRAY", "items": _obj(title=_STR, dates=_STR, place=_STR, bullets=_LIST)})},
    projects={"type": "ARRAY", "items": _obj(name=_STR, tech=_STR, dates=_STR, links=_LIST, bullets=_LIST)},
    skills={"type": "ARRAY", "items": _obj(label=_STR, items=_LIST)},
    sections={"type": "ARRAY", "items": _obj(heading=_STR, bullets=_LIST)},
    headings=_obj(**{k: _STR for k in BUILTIN}),
    order=_LIST,
)

EXTRACT_SYSTEM = """You convert resume text into JSON. You are a copier, not an editor.

Rules:
- Copy every word exactly as written: same spelling, capitals, punctuation, numbers and symbols. Never fix,
  shorten, reword, merge, split or add anything. If the text has a typo, keep the typo.
- A bullet that wraps onto the next line is one bullet: join its lines with a single space. Drop the bullet symbol.
- experience: one item per company. A company with several roles (e.g. intern, then full-time) has several roles,
  newest first as written. Put company-level dates in "dates" and role dates in each role.
- An internship counts as experience. Keep the job title exactly, e.g. "Web Developer Intern".
- projects: "tech" is the tools line exactly as written; "links" are the link words exactly as written next to the
  project name (e.g. "Live", "GitHub", "Demo") or placeholder tokens like [GITHUB-2]. Empty list when there are none.
- skills: keep the resume's own categories, labels and item order. Split items on commas. No label -> "".
- sections: every other section (achievements, certifications, publications, positions of responsibility,
  extra-curricular...) with its heading exactly as written and its bullets.
- headings: the resume's own heading text for summary, education, experience, projects and skills.
- order: the order sections appear in, using "summary", "education", "experience", "projects", "skills", and the
  exact heading of each item in "sections".
- summary: the summary / objective paragraph exactly, or "" when there is none.
- Leave out contact details, placeholders such as [EMAIL] or [PHONE] in the header, and personal details
  (address, date of birth, declaration).
- Fill every field. Use "" (or []) only when the resume really has nothing for it. Dates, degree, place and score
  are usually on the line next to the school, company or role: copy them."""


# ---------------------------------------------------------------------------- reading the original

def _norm(text: str) -> str:
    """For 'is this bullet really in the resume?': case, whitespace, quotes, dashes and bullets don't matter."""
    text = (text or "").lower().translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-",
                                                         "—": "-", "•": "", "●": "", "▪": "", "·": ""}))
    return re.sub(r"[\s\-]+", "", text)


def header_lines(text: str) -> list[str]:
    """The lines above the first section heading: your name and contact line(s)."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    start = next((i for i, ln in enumerate(lines[:15]) if _HEADING.match(ln) and len(ln) < 45), None)
    return [ln for ln in lines[:start or 0] if ln]


def pdf_links(path: Path) -> list[str]:
    """Every hyperlink in a PDF, in reading order (top to bottom, then left to right)."""
    if path.suffix.lower() != ".pdf" or not path.exists():
        return []
    from pypdf import PdfReader

    found = []
    for page_no, page in enumerate(PdfReader(str(path)).pages):
        for annot in page.get("/Annots") or []:
            annot = annot.get_object()
            uri = (annot.get("/A") or {}).get("/URI")
            rect = annot.get("/Rect")
            if uri and rect:
                found.append((page_no, -round(float(rect[3])), round(float(rect[0])), str(uri)))
    return [uri for *_, uri in sorted(found)]


def _url_for(item: str, uris: list[str]) -> str:
    low = item.lower().strip()
    if _EMAIL.fullmatch(item.strip()):
        return "mailto:" + item.strip()
    bare = re.sub(r"^(https?://)?(www\.)?", "", low).rstrip("/")
    for uri in uris:
        if bare and len(bare) > 4 and bare in uri.lower():
            return uri
    word = re.sub(r"[^a-z]", "", low)
    for uri in uris:
        host = urlparse(uri).netloc.lower().replace("www.", "")
        if len(word) >= 4 and word in host.replace(".", ""):
            return uri
    if re.fullmatch(r"(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?", low):
        return item if low.startswith("http") else "https://" + item.strip()
    return ""


def contact_from(text: str, uris: list[str]) -> tuple[str, list[list[dict]]]:
    """(name, contact lines) from the top of the resume, each line a list of {text, url}. Stays on this machine."""
    lines = [ln for ln in header_lines(text) if not _NOT_A_NAME.match(ln)]  # skip a "CURRICULUM VITAE" title
    if not lines:
        return "", []
    name = lines[0] if _NAME_LINE.match(lines[0]) and len(lines[0]) <= 40 else ""
    out = []
    for ln in lines[1:] if name else lines:
        if _PERSONAL_LINE.match(ln):
            continue
        items = [{"text": part, "url": _url_for(part, uris)}
                 for part in (p.strip(" ,") for p in re.split(r"\s*[|•·◦]\s*|\s{3,}", ln)) if part]
        if items:
            out.append(items)
    return name, out


def _restore(value, redacted: Redacted):
    if isinstance(value, str):
        return redacted.restore(value)
    if isinstance(value, list):
        return [_restore(v, redacted) for v in value]
    if isinstance(value, dict):
        return {k: _restore(v, redacted) for k, v in value.items()}
    return value


def build(data: dict, source_text: str, redacted: Redacted, uris: list[str]) -> tuple[dict, list[str]]:
    """Turn the AI's JSON into profile/resume.yaml content: real contact details and links filled in locally, and
    every line checked against the original. Returns (cv, warnings)."""
    data = _restore(data, redacted)
    name, contact = contact_from(source_text, uris)
    used = {c["url"] for line in contact for c in line if c["url"]}
    body_uris = [u for u in uris if u not in used and not u.startswith("mailto:")]
    warnings: list[str] = []

    written = re.compile(r"(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)  # a link written out in full
    words = [str(x).strip() for p in data.get("projects") or [] for x in p.get("links") or []
             if not written.match(str(x).strip())]
    assign = len(words) == len(body_uris)  # "Live | GitHub" words get the PDF's links in reading order
    if words and not assign:
        warnings.append(f"the PDF has {len(body_uris)} links below the header for {len(words)} project link words: "
                        "add the urls under projects in profile/resume.yaml")
    for entry in data.get("projects") or []:
        links = []
        for label in entry.get("links") or []:
            label = str(label).strip()
            if written.match(label):
                url = label if label.lower().startswith("http") else "https://" + label
            else:
                url = body_uris.pop(0) if assign else ""
            links.append({"text": label, "url": url})
        entry["links"] = links

    cv = {"name": name or redacted.values.get("[NAME]", ""), "contact": contact}
    cv.update({k: data.get(k) for k in ("summary", "education", "experience", "projects", "skills", "sections")})
    cv["headings"] = {k: v for k, v in (data.get("headings") or {}).items() if v}
    cv["order"] = data.get("order") or ["education", "experience", "projects", "skills"]

    cv = normalize(cv)
    hay = _norm(source_text)
    for where, text in list(lines_of(cv)) + list(_fields_of(cv)):
        if text and _norm(text) not in hay:
            warnings.append(f"not found word-for-word in your resume ({where}): {text[:90]}")
    return cv, warnings


def _fields_of(cv: dict):
    """Names, dates, scores, titles and skills: checked like bullets, so "CGPA 9.7" for 8.7 or a "Senior" title for
    an internship is caught too."""
    for e in cv.get("education") or []:
        for key in ("school", "degree", "dates", "score", "place"):
            yield f"education {key}", e.get(key, "")
    for e in cv.get("experience") or []:
        for key in ("company", "dates", "place"):
            yield f"experience {key}", e.get(key, "")
        for r in e.get("roles") or []:
            for key in ("title", "dates"):
                yield f"role {key}", r.get(key, "")
    for p in cv.get("projects") or []:
        yield "project name", p.get("name", "")
        for item in re.split(r",\s*", p.get("tech", "")):
            yield "project tech", item
    for row in cv.get("skills") or []:
        for item in row.get("items") or []:
            yield f"skill ({row.get('label') or 'skills'})", item
    for sec in cv.get("sections") or []:
        yield "section heading", sec.get("heading", "")


# ---------------------------------------------------------------------------- the YAML file

def lines_of(cv: dict):
    """(where, text) for every piece of free text in the resume body: what tailoring may touch."""
    if cv.get("summary"):
        yield "summary", cv["summary"]
    for i, e in enumerate(cv.get("experience") or []):
        for j, role in enumerate(e.get("roles") or []):
            for k, b in enumerate(role.get("bullets") or []):
                yield f"experience {e.get('company')}, {role.get('title')}", b
    for p in cv.get("projects") or []:
        for b in p.get("bullets") or []:
            yield f"project {p.get('name')}", b
    for s in cv.get("sections") or []:
        for b in s.get("bullets") or []:
            yield s.get("heading", "section"), b


HEADER = """# Your resume as structured data, built by `jobradar resume --setup` from {source}.
# `jobradar draft <id>` makes a tailored copy of this for one job: it reorders projects, bullets and skills, may
# lightly reword a few bullets (never adding numbers or tools you don't list here), and trims to fit the page.
# Edit freely: fix anything the warnings point at, add links, change the order. This file is git-ignored.
# Check the result with `jobradar resume --pdf`.
"""


def save(cv: dict, path: Path, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(cv, sort_keys=False, allow_unicode=True, width=120, default_flow_style=False)
    path.write_text(HEADER.format(source=source) + body, encoding="utf-8")


def _text(value) -> str:
    return "" if value is None or isinstance(value, (dict, list)) else str(value).strip()


def _texts(values) -> list[str]:
    return [t for t in (_text(v) for v in (values if isinstance(values, list) else [])) if t]


def _dicts(values) -> list[dict]:
    return [v for v in (values if isinstance(values, list) else []) if isinstance(v, dict)]


def _link(x) -> dict:
    return {"text": _text(x.get("text")), "url": _text(x.get("url"))} if isinstance(x, dict) else {"text": _text(x), "url": ""}


def normalize(data) -> dict:
    """Every value the shape and type the code expects, whatever a hand edit did: CGPA 8.7 (a number) becomes
    "8.7", an empty "-" bullet goes, a missing section is an empty list."""
    data = data if isinstance(data, dict) else {}
    contact = data.get("contact") if isinstance(data.get("contact"), list) else []
    lines = contact if contact and all(isinstance(x, list) for x in contact) else [contact]
    return {
        "name": _text(data.get("name")),
        "contact": [[_link(x) for x in line if _link(x)["text"]] for line in lines if line],
        "summary": _text(data.get("summary")),
        "education": [{k: _text(e.get(k)) for k in ("school", "place", "degree", "dates", "score")}
                      | {"details": _texts(e.get("details"))} for e in _dicts(data.get("education"))],
        "experience": [{k: _text(e.get(k)) for k in ("company", "place", "dates")}
                       | {"roles": [{k: _text(r.get(k)) for k in ("title", "dates", "place")}
                                    | {"bullets": _texts(r.get("bullets"))} for r in _dicts(e.get("roles"))]}
                       for e in _dicts(data.get("experience"))],
        "projects": [{k: _text(p.get(k)) for k in ("name", "tech", "dates")}
                     | {"links": [_link(x) for x in (p.get("links") if isinstance(p.get("links"), list) else [])
                                  if _link(x)["text"]], "bullets": _texts(p.get("bullets"))}
                     for p in _dicts(data.get("projects"))],
        "skills": [{"label": _text(r.get("label")), "items": _texts(r.get("items"))} for r in _dicts(data.get("skills"))],
        "sections": [{"heading": _text(x.get("heading")), "bullets": _texts(x.get("bullets"))}
                     for x in _dicts(data.get("sections"))],
        "headings": {str(k): _text(v) for k, v in (data.get("headings") or {}).items()}
        if isinstance(data.get("headings"), dict) else {},
        "order": _texts(data.get("order")) or ["education", "experience", "projects", "skills"],
    }


def load(path: Path) -> dict | None:
    if not path.exists():
        return None
    return normalize(yaml.safe_load(path.read_text(encoding="utf-8")))


def as_text(cv: dict) -> str:
    """Plain-text resume: what portals' 'paste your resume' boxes want, and what the AI sees for tailoring."""
    out = []
    heads = {"summary": "Summary", "education": "Education", "experience": "Experience", "projects": "Projects",
             "skills": "Skills"} | (cv.get("headings") or {})
    for key in section_order(cv):
        if key == "summary" and cv.get("summary"):
            out += [heads["summary"].upper(), cv["summary"], ""]
        elif key == "education" and cv.get("education"):
            out.append(heads["education"].upper())
            for e in cv["education"]:
                out.append(" | ".join(x for x in (e.get("school"), e.get("place"), e.get("dates")) if x))
                out.append(" | ".join(x for x in (e.get("degree"), e.get("score")) if x))
                out += [f"- {d}" for d in e.get("details") or []]
            out.append("")
        elif key == "experience" and cv.get("experience"):
            out.append(heads["experience"].upper())
            for e in cv["experience"]:
                out.append(" | ".join(x for x in (e.get("company"), e.get("place"), e.get("dates")) if x))
                for role in e.get("roles") or []:
                    out.append(" | ".join(x for x in (role.get("title"), role.get("dates")) if x))
                    out += [f"- {b}" for b in role.get("bullets") or []]
            out.append("")
        elif key == "projects" and cv.get("projects"):
            out.append(heads["projects"].upper())
            for p in cv["projects"]:
                links = " ".join(x["url"] for x in p.get("links") or [] if x.get("url"))
                out.append(" | ".join(x for x in (p.get("name"), p.get("tech"), p.get("dates"), links) if x))
                out += [f"- {b}" for b in p.get("bullets") or []]
            out.append("")
        elif key == "skills" and cv.get("skills"):
            out.append(heads["skills"].upper())
            out += [(f"{s['label']}: " if s.get("label") else "") + ", ".join(s.get("items") or []) for s in cv["skills"]]
            out.append("")
        else:
            sec = next((s for s in cv.get("sections") or [] if s.get("heading") == key), None)
            if sec and sec.get("bullets"):
                out += [sec["heading"].upper()] + [f"- {b}" for b in sec["bullets"]] + [""]
    return "\n".join(out).strip()


def section_order(cv: dict) -> list[str]:
    known = [k for k in cv.get("order") or [] if k in BUILTIN or any(s.get("heading") == k for s in cv.get("sections") or [])]
    rest = [k for k in BUILTIN if k not in known and cv.get(k)]
    rest += [s["heading"] for s in cv.get("sections") or [] if s.get("heading") not in known]
    return list(dict.fromkeys(known + rest))


def resume_source_path(settings) -> Path | None:
    """The resume file jobradar reads (for its links), if it is a file in the project."""
    from .config import resume_text

    _, source = resume_text(settings)
    path = settings.root / source
    return path if path.exists() else None


async def extract(text: str, gemini) -> tuple[dict, str]:
    """The AI sorts (redacted) resume text into sections. Returns (data, model)."""
    return await gemini.generate_json(EXTRACT_SYSTEM, f"<resume>\n{text}\n</resume>", CV_SCHEMA,
                                      max_tokens=8192, temperature=0.0, patient=False)
