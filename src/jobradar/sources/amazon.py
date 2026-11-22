"""amazon.jobs search in your country (profile locations.country). robots.txt only disallows /internal paths;
checked every run anyway."""

from __future__ import annotations

import re

from ..http import Blocked
from ..models import Job
from ..util import country_code, date_from_text, html_to_text, with_country

API = "https://www.amazon.jobs/en/search.json"
PAGE = 100
MAX_PAGES = 4
DEFAULT_QUERIES = ("software development engineer", "machine learning")


def parse_job(j: dict) -> Job:
    parts = [html_to_text(j.get("description"))]
    if j.get("basic_qualifications"):
        parts.append("Basic qualifications:\n" + html_to_text(j["basic_qualifications"]))
    if j.get("preferred_qualifications"):
        parts.append("Preferred qualifications:\n" + html_to_text(j["preferred_qualifications"]))
    loc = j.get("normalized_location") or j.get("location") or ""
    if not re.search(rf"\b{re.escape(j.get('country_code') or '-')}\b", loc):
        loc = with_country(loc, j.get("country_code"))
    university = str(j.get("university_job")).lower() in ("1", "true")
    return Job(
        source="amazon", board="amazon", company="Amazon",  # company_name is the legal entity (e.g. "ADCI - Karnataka")
        native_id=str(j.get("id_icims") or j.get("id")), title=(j.get("title") or "").strip(),
        url="https://www.amazon.jobs" + (j.get("job_path") or ""), location=loc,
        posted_at=date_from_text(j.get("posted_date")),
        description="\n\n".join(p for p in parts if p),
        employment_type="Intern" if str(j.get("is_intern")).lower() in ("1", "true") else (j.get("job_schedule_type") or ""),
        department=j.get("job_category") or "", level_hint="university" if university else "",
    )


async def fetch(http, target) -> list[Job]:
    if not await http.robots_allowed(API):
        raise Blocked(f"robots.txt disallows {API}")
    seen: dict[str, Job] = {}
    code = country_code(target.opts.get("country", "India"), alpha=3)
    country = {"country": code} if code else {}  # unknown country: search everywhere, the location filter decides
    for query in target.opts.get("queries") or DEFAULT_QUERIES:
        offset = 0
        for _ in range(MAX_PAGES):
            data = await http.get_json(API, params={"base_query": query, **country, "result_limit": PAGE,
                                                    "offset": offset, "sort": "recent"})
            batch = data.get("jobs") or []
            for j in batch:
                job = parse_job(j)
                seen.setdefault(job.native_id, job)
            offset += PAGE
            if not batch or offset >= (data.get("hits") or 0):
                break
    return list(seen.values())
