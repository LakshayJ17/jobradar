"""Generic web scraper for job boards and career pages that publish schema.org JobPosting data.

Most job pages embed a JobPosting JSON-LD block (it is what Google for Jobs reads), so one scraper covers
many sites: it reads a listing page, collects job links, then reads each new job page's JobPosting.

Config (config/sources.yaml -> scrape:):
  - name: Internshala
    list_urls: [https://internshala.com/fresher-jobs/software-development-jobs/]
    link_pattern: '^/job/detail/'          # regex on the link path
    title_from_url: '/job/detail/(?:fresher-)?(.+?)-job'   # optional: title hint for pre-filtering
    max_details: 40                        # new job pages fetched per run (politeness)

robots.txt and the no-fetch list are enforced on every request (see http.py).
"""

from __future__ import annotations

import html as htmllib
import json
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from ..models import Job
from ..salary import from_schema, parse_text
from ..util import clip, country_name, date_from_iso, html_to_text

COMPLETE_LISTING = False  # listing pages show the newest jobs only, so "missing" does not mean "closed"
_LD_RX = re.compile(r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>", re.S | re.I)
_A_RX = re.compile(r"<a\b[^>]*?href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.S | re.I)
_SKIP_TEXT = re.compile(r"^(apply|view|save|share|details?|more|read more|know more)\b", re.I)


def ld_blocks(page: str) -> list:
    out = []
    for raw in _LD_RX.findall(page or ""):
        raw = raw.strip()
        for candidate in (raw, re.sub(r"[\x00-\x1f]+", " ", raw)):  # some sites put raw newlines in strings
            try:
                out.append(json.loads(candidate))
                break
            except json.JSONDecodeError:
                continue
    return out


def _walk(node):
    stack = [node]
    while stack:
        it = stack.pop()
        if isinstance(it, list):
            stack.extend(it)
        elif isinstance(it, dict):
            yield it
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                if isinstance(it.get(key), (list, dict)):
                    stack.append(it[key])


def _types(it: dict) -> set[str]:
    t = it.get("@type")
    return set(t) if isinstance(t, list) else {t}


def job_postings(page: str) -> list[dict]:
    return [it for block in ld_blocks(page) for it in _walk(block) if "JobPosting" in _types(it)]


def canonical(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") or "/", "", ""))


def listing_links(page: str, base_url: str, pattern: str) -> dict[str, str]:
    """{canonical job url: title hint} from JSON-LD ItemLists and <a> tags whose path matches `pattern`."""
    rx = re.compile(pattern)
    links: dict[str, str] = {}

    def add(url: str, text: str) -> None:
        absolute = urljoin(base_url, htmllib.unescape(url))
        if urlsplit(absolute).netloc != urlsplit(base_url).netloc or not rx.search(urlsplit(absolute).path):
            return
        key = canonical(absolute)
        text = re.sub(r"\s+", " ", text or "").strip()
        current = links.get(key, "")
        if not current or (text and 3 < len(text) < len(current) and not _SKIP_TEXT.match(text)):
            links[key] = text

    for block in ld_blocks(page):
        for it in _walk(block):
            if "ListItem" in _types(it):
                item = it.get("item")
                url = it.get("url") or (item.get("url") if isinstance(item, dict) else item if isinstance(item, str) else None)
                if url:
                    add(url, it.get("name") or (item.get("name", "") if isinstance(item, dict) else ""))
    for href, inner in _A_RX.findall(page or ""):
        add(href, htmllib.unescape(re.sub(r"<[^>]+>", " ", inner)))
    return links


def _text(value) -> str:
    if isinstance(value, list):
        return "\n".join(_text(v) for v in value)
    if isinstance(value, dict):
        return _text(value.get("name") or value.get("description") or "")
    return html_to_text(str(value)) if value else ""


def _location(posting: dict) -> tuple[str, bool]:
    places = posting.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts = []
    for place in places:
        addr = place.get("address") if isinstance(place, dict) else place
        if isinstance(addr, str):
            parts.append(addr)
        elif isinstance(addr, dict):
            country = addr.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            country = country_name(country) or country
            parts.append(", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x))
    remote = "TELECOMMUTE" in str(posting.get("jobLocationType") or "").upper()
    if remote:
        req = posting.get("applicantLocationRequirements") or []
        req = req if isinstance(req, list) else [req]
        names = [r.get("name") if isinstance(r, dict) else str(r) for r in req]
        parts.append("Remote" + (f" ({', '.join(n for n in names if n)})" if any(names) else ""))
    return " / ".join(dict.fromkeys(p for p in parts if p)), remote


def _min_years(posting: dict) -> int | None:
    exp = posting.get("experienceRequirements")
    if isinstance(exp, dict) and exp.get("monthsOfExperience") not in (None, ""):
        try:
            return int(float(exp["monthsOfExperience"]) // 12)
        except (TypeError, ValueError):
            return None
    return None


def apply_posting(job: Job, posting: dict) -> None:
    """Fill a Job from a schema.org JobPosting."""
    job.title = clip(_text(posting.get("title")) or job.title, 200)
    org = posting.get("hiringOrganization")
    org_name = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else "")
    if org_name and job.detail.get("company_from_page", True):  # job boards: the employer; career sites: keep name
        job.company = org_name.strip()
    loc, remote = _location(posting)
    job.location = loc or job.location
    job.remote = True if remote else job.remote
    job.posted_at = date_from_iso(str(posting.get("datePosted") or "")) or job.posted_at
    job.expires = date_from_iso(str(posting.get("validThrough") or "")) or job.expires
    etype = posting.get("employmentType")
    job.employment_type = ", ".join(etype) if isinstance(etype, list) else str(etype or job.employment_type or "")
    job.salary = from_schema(posting.get("baseSalary")) or job.salary  # many boards put 0 here = not disclosed
    parts = []
    years = _min_years(posting)
    if years is not None:
        parts.append(f"Experience required: {years} years of experience.")  # parsed by the rule filter
    elif isinstance(posting.get("experienceRequirements"), str):
        parts.append(f"Experience: {posting['experienceRequirements']}")
    parts.append(_text(posting.get("description")))
    for key in ("responsibilities", "qualifications", "skills", "educationRequirements"):
        if posting.get(key):
            parts.append(f"{key.capitalize()}: {_text(posting[key])}")
    job.description = "\n\n".join(p for p in parts if p)


def _title_hint(url: str, text: str, title_rx: re.Pattern | None) -> str:
    if title_rx:
        m = title_rx.search(urlsplit(url).path)
        if m:
            return m.group(1).replace("-", " ").strip()
    return clip(text, 140) or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")


async def fetch(http, target) -> list[Job]:
    o = target.opts
    title_rx = re.compile(o["title_from_url"]) if o.get("title_from_url") else None
    jobs: dict[str, Job] = {}
    for list_url in o.get("list_urls") or []:
        page = await http.get_html(list_url)
        for url, text in listing_links(page, list_url, o.get("link_pattern") or ".").items():
            if url in jobs:
                continue
            path = urlsplit(url).path.rstrip("/")
            jobs[url] = Job(
                source="scrape", board=target.board, company=target.name, native_id=path,
                title=_title_hint(url, text, title_rx), url=url,
                needs_detail=True, detail={"url": url, "cap": int(o.get("max_details", 40)),
                                           "company_from_page": bool(o.get("company_from_page", True)),
                                           "salary_label": o.get("salary_label")},
            )
    return list(jobs.values())


async def enrich(http, job: Job) -> None:
    page = await http.get_html(job.detail["url"])
    postings = job_postings(page)
    if not postings:
        raise ValueError("no JobPosting data on the page")
    apply_posting(job, postings[0])
    if not job.salary and job.detail.get("salary_label"):
        job.salary = salary_from_label(page, job.detail["salary_label"])


def salary_from_label(page: str, label_rx: str) -> list[float] | None:
    """Pay printed after a label on the page, e.g. 'CTC (ANNUAL)' -> '₹ 2,00,000 - 2,40,000 /year'.
    Stops at the next quote so embedded JSON such as "salary_text":"[Salary Hidden]" is read as unknown."""
    m = re.search(label_rx, page or "")
    if not m:
        return None
    tail = re.split(r'\\?"', page[m.end(): m.end() + 800])[0]  # embedded JSON: stop at the closing quote
    parts = [x.strip() for x in htmllib.unescape(re.sub(r"<[^>]+>", "\x00", tail)).split("\x00") if x.strip()]
    if not parts:
        return None
    value = parts[0]  # only the element right after the label ("Competitive salary" -> unknown)
    if len(parts) > 1 and re.match(r"^/?\s*(?:per\s+)?(?:year|month|annum|yr|mo)\b", parts[1], re.I):
        value += " " + parts[1]
    return parse_text(value[:140])
