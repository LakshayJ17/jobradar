"""Small helpers: HTML -> text and date normalisation for the different job boards."""

from __future__ import annotations

import html
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "table"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: D401 - HTMLParser API
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Convert (possibly entity-escaped) HTML to readable plain text."""
    if not value:
        return ""
    if "<" not in value and "&lt;" in value:  # Greenhouse returns escaped HTML
        value = html.unescape(value)
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[^\S\n]+", " ", text)  # every kind of space (&emsp;, &thinsp;...), not just ASCII ones
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Job text comes from strangers. Before it is stored, printed or rendered: no terminal escape sequences (which can
# write your clipboard or fake output), no other control characters, no zero-width or bidi-override characters
# (which disguise text), and no line breaks inside one-line fields (which can fake CI annotations).
_UNSAFE = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\ufffe\uffff"
                     "\ud800-\udfff]")  # ... and noncharacters / lone surrogates, which break XML (docx, xlsx)


def clean_block(text) -> str:
    """Multi-line text (descriptions): line breaks and tabs kept, every other control character removed."""
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    return _UNSAFE.sub("", text)


def clean_line(text) -> str:
    """One-line text (titles, companies, locations, URLs): control characters removed, all whitespace one space."""
    return re.sub(r"\s+", " ", clean_block(text)).strip()


def safe_url(url) -> str:
    """The URL if it is a plain http(s) link, else ''. Used wherever a job's link becomes clickable."""
    url = clean_line(url)
    return url if re.fullmatch(r"https?://[^\s<>\"'`\\]+", url, re.I) and len(url) <= 2000 else ""


def tag_blocks(page: str, opening: re.Pattern, closing: str, *, window: int | None = None) -> list[tuple[re.Match, str]]:
    """(opening-tag match, inner text) for each block, in linear time. A lazy regex like "<script>(.*?)</script>"
    rescans the rest of the page for every opening tag that is never closed (quadratic on a hostile page); here the
    closing tag is found with a plain string search, and the scan stops at the first block that never closes.
    window: give up on a block whose closing tag isn't within this many characters (and carry on)."""
    page = page or ""
    low = page.lower()
    closing = closing.lower()
    out = []
    for m in opening.finditer(page):
        limit = m.end() + window if window else len(page)
        end = low.find(closing, m.end(), limit)
        if end < 0:
            if window:
                continue
            break
        out.append((m, page[m.end():end]))
    return out


def today() -> date:
    return datetime.now(timezone.utc).date()


def date_from_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        m = re.match(r"\d{4}-\d{2}-\d{2}", value)
        return m.group(0) if m else None


def date_from_epoch(value, *, millis: bool = False) -> str | None:
    if value in (None, ""):
        return None
    try:
        seconds = float(value) / (1000 if millis else 1)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def date_from_text(value: str | None, fmt: str = "%B %d, %Y") -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), fmt).date().isoformat()
    except ValueError:
        return None


def date_from_workday(value: str | None, ref: date | None = None) -> str | None:
    """Workday shows 'Posted Today', 'Posted Yesterday', 'Posted 3 Days Ago', 'Posted 30+ Days Ago'."""
    if not value:
        return None
    s = value.lower()
    ref = ref or today()
    if "today" in s:
        days = 0
    elif "yesterday" in s:
        days = 1
    else:
        m = re.search(r"(?<!\d)(\d{1,4})(\+?)\s*days?", s[:200])
        if not m:
            return None
        days = int(m.group(1)) + (1 if m.group(2) else 0)  # "30+ days" is older than 30
    return (ref - timedelta(days=days)).isoformat()


def date_from_ago(value: str | None, ref: date | None = None) -> str | None:
    """'Posted 2 weeks ago', '3 days ago', 'today', 'just now', 'a month ago' -> YYYY-MM-DD."""
    s = (value or "").lower()[:120]
    ref = ref or today()
    if re.search(r"\b(?:today|just now|hours? ago|minutes? ago|few hours)\b", s):
        return ref.isoformat()
    if "yesterday" in s:
        return (ref - timedelta(days=1)).isoformat()
    m = re.search(r"\b(\d{1,3}|an?|one)\+?\s*(day|week|month)s?\s+ago", s)
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
    return (ref - timedelta(days=n * {"day": 1, "week": 7, "month": 30}[m.group(2)])).isoformat()


def days_ago(iso: str | None, ref: date | None = None) -> int | None:
    if not iso:
        return None
    try:
        return ((ref or today()) - date.fromisoformat(iso[:10])).days
    except ValueError:
        return None


def clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------- countries
# name -> (ISO 3166 alpha-2, alpha-3, other names boards use). Used to search each career site in the country set in
# profile.yaml (locations.country) and to add the country to a location the board only gives as a code.
COUNTRIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "India": ("IN", "IND", ()),
    "United States": ("US", "USA", ("United States of America", "USA", "US")),
    "United Kingdom": ("GB", "GBR", ("UK", "Great Britain", "England")),
    "Canada": ("CA", "CAN", ()),
    "Germany": ("DE", "DEU", ("Deutschland",)),
    "Ireland": ("IE", "IRL", ()),
    "Netherlands": ("NL", "NLD", ("The Netherlands",)),
    "France": ("FR", "FRA", ()),
    "Spain": ("ES", "ESP", ()),
    "Portugal": ("PT", "PRT", ()),
    "Italy": ("IT", "ITA", ()),
    "Poland": ("PL", "POL", ()),
    "Romania": ("RO", "ROU", ()),
    "Czech Republic": ("CZ", "CZE", ("Czechia",)),
    "Sweden": ("SE", "SWE", ()),
    "Switzerland": ("CH", "CHE", ()),
    "Israel": ("IL", "ISR", ()),
    "United Arab Emirates": ("AE", "ARE", ("UAE",)),
    "Singapore": ("SG", "SGP", ()),
    "Malaysia": ("MY", "MYS", ()),
    "Indonesia": ("ID", "IDN", ()),
    "Philippines": ("PH", "PHL", ()),
    "Vietnam": ("VN", "VNM", ("Viet Nam",)),
    "Japan": ("JP", "JPN", ()),
    "South Korea": ("KR", "KOR", ("Korea", "Republic of Korea")),
    "China": ("CN", "CHN", ()),
    "Australia": ("AU", "AUS", ()),
    "New Zealand": ("NZ", "NZL", ()),
    "Pakistan": ("PK", "PAK", ()),
    "Bangladesh": ("BD", "BGD", ()),
    "Sri Lanka": ("LK", "LKA", ()),
    "Nepal": ("NP", "NPL", ()),
    "South Africa": ("ZA", "ZAF", ()),
    "Nigeria": ("NG", "NGA", ()),
    "Kenya": ("KE", "KEN", ()),
    "Egypt": ("EG", "EGY", ()),
    "Brazil": ("BR", "BRA", ()),
    "Mexico": ("MX", "MEX", ()),
    "Argentina": ("AR", "ARG", ()),
    "Colombia": ("CO", "COL", ()),
}
_COUNTRY_LOOKUP = {k.lower(): name for name, (a2, a3, other) in COUNTRIES.items()
                   for k in (name, a2, a3, *other)}


def country_name(value: str | None) -> str:
    """'IN', 'IND', 'india' or 'United States of America' -> the display name ('India', 'United States').
    An unknown code returns ''; an unknown name is returned as given."""
    v = (value or "").strip()
    if not v:
        return ""
    if v.lower() in _COUNTRY_LOOKUP:
        return _COUNTRY_LOOKUP[v.lower()]
    return "" if len(v) <= 3 and v.isupper() else v


def country_names(value: str | None) -> list[str]:
    """Every lowercase name a board might use for this country (for matching facets and location text)."""
    name = country_name(value)
    if name not in COUNTRIES:
        return [name.lower()] if name else []
    return list(dict.fromkeys(x.lower() for x in (name, *COUNTRIES[name][2]) if len(x) > 2 or x == "UK"))


def country_code(value: str | None, *, alpha: int = 2) -> str:
    """ISO alpha-2 (or alpha-3) code for a country name or code; '' when unknown."""
    name = country_name(value)
    if name not in COUNTRIES:
        return ""
    return COUNTRIES[name][0 if alpha == 2 else 1]


def with_country(location: str, country: str | None) -> str:
    """Append the country to a location string that doesn't already name it: ('Bengaluru', 'IN') -> 'Bengaluru, India'."""
    name = country_name(country)
    if not name:
        return location
    names = country_names(name) + [name.lower()]
    if any(re.search(rf"\b{re.escape(n)}\b", location or "", re.I) for n in names):
        return location
    return f"{location}, {name}" if location else name
