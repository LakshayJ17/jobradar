"""Google Careers (scraped HTML). Google's terms allow automated access that follows robots.txt; robots.txt allows
the results pages but NOT the `page=` parameter, so we never paginate - instead we read the first page of several
narrower searches. Jobs are read from the data block the page embeds (AF_initDataCallback 'ds:1')."""

from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from ..http import FetchError
from ..models import Job
from ..util import country_name, date_from_epoch, html_to_text

COMPLETE_LISTING = False  # first pages only (robots.txt disallows paging)
RESULTS = "https://www.google.com/about/careers/applications/jobs/results"
INTERN_LEVEL = "INTERN_AND_APPRENTICE"
DEFAULT_QUERIES = ("", "software engineer", "software developer", "machine learning", "data", "site reliability",
                   "front end", "back end", "cloud", "security")


def _at(row, *path, default=None):
    cur = row
    for i in path:
        if not isinstance(cur, list) or i >= len(cur):
            return default
        cur = cur[i]
    return default if cur is None else cur


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_page(page: str, level: str = "") -> tuple[list[Job], int, int]:
    """(jobs, total results, page size) from one results page."""
    start = page.find("AF_initDataCallback({key: 'ds:1'")  # plain string search: linear on any page
    begin = page.find("data:[", start) if start >= 0 else -1
    end = page.find("], sideChannel", begin) if begin >= 0 else -1
    if end < 0:
        raise FetchError("Google Careers page layout changed (job data block not found)")
    data = json.loads(page[begin + len("data:"): end + 1])
    rows = _at(data, 0, default=[])
    total = _at(data, 2, default=len(rows))
    size = _at(data, 3, default=20) or 20
    jobs = []
    for row in rows:
        jid, title = str(_at(row, 0, default="")), _at(row, 1, default="")
        if not jid or not title:
            continue
        locations = [_at(loc, 0, default="") for loc in _at(row, 9, default=[])]
        parts = [html_to_text(_at(row, 10, 1, default="")),
                 "Responsibilities:\n" + html_to_text(_at(row, 3, 1, default="")),
                 html_to_text(_at(row, 4, 1, default=""))]
        jobs.append(Job(
            source="google", board="google", company=_at(row, 7, default="Google"), native_id=jid,
            title=title.strip(), url=f"{RESULTS}/{jid}-{slugify(title)}",
            location=" / ".join(dict.fromkeys(x for x in locations if x)),
            posted_at=date_from_epoch(_at(row, 12, 0)) or date_from_epoch(_at(row, 14, 0)),
            description="\n\n".join(p for p in parts if p.strip() and p.strip() != "Responsibilities:"),
            level_hint={"EARLY": "entry_level", INTERN_LEVEL: "internship"}.get(level, ""),
            employment_type="Intern" if level == INTERN_LEVEL else "",
        ))
    return jobs, int(total or 0), int(size)


def levels(opts: dict) -> list[str]:
    """Experience levels to search: sources.yaml levels, plus internships when profile.yaml asks for them."""
    mode = opts.get("internships")
    if mode == "only":
        return [INTERN_LEVEL]
    base = list(opts.get("levels") or ["EARLY"])
    return base + [INTERN_LEVEL] if mode and INTERN_LEVEL not in base else base


async def fetch(http, target) -> list[Job]:
    jobs: dict[str, Job] = {}
    for level in levels(target.opts):
        # internships are few: one broad search finds them all, and keeps requests down
        for q in ("",) if level == INTERN_LEVEL else target.opts.get("queries") or DEFAULT_QUERIES:
            where = target.opts.get("location") or country_name(target.opts.get("country", "India"))
            query = urlencode({"q": q, **({"location": where} if where else {}), "target_level": level})
            page_jobs, _total, _size = parse_page(await http.get_html(f"{RESULTS}?{query}"), level)
            for job in page_jobs:
                jobs.setdefault(job.native_id, job)
    return list(jobs.values())
