"""Workday career sites (<tenant>.<wd>.myworkdayjobs.com). Uses the same JSON the public site loads,
only after robots.txt allows it. Reads your country's postings via the site's own location facet."""

from __future__ import annotations

import re

from ..http import Blocked
from ..models import Job
from ..util import country_name, country_names, date_from_iso, date_from_workday, html_to_text

PAGE = 20  # Workday's maximum page size
MAX_PAGES_COUNTRY = 40
FALLBACK_QUERIES = ("software engineer", "developer", "machine learning", "graduate")


def base_url(o: dict) -> str:
    return f"https://{o['tenant']}.{o['wd']}.myworkdayjobs.com"


def api_url(o: dict) -> str:
    return f"{base_url(o)}/wday/cxs/{o['tenant']}/{o['site']}"


def find_country_facet(facets: list | None, country: str = "India") -> tuple[str, str] | None:
    """Locate the (facetParameter, id) whose value is the given country, wherever the tenant nests it.
    Matches the names tenants use, e.g. "United States of America" for United States."""
    names = set(country_names(country))
    for facet in facets or []:
        param = facet.get("facetParameter")
        for value in facet.get("values") or []:
            if "values" in value:
                found = find_country_facet([value], country)
                if found:
                    return found
            elif param and names and (value.get("descriptor") or "").strip().lower() in names:
                return param, value.get("id")
    return None


def parse_posting(p: dict, target, country: str = "") -> Job:
    """country: set when the list was filtered to that country, so every posting is in it."""
    o = target.opts
    path = p.get("externalPath") or ""
    loc = (p.get("locationsText") or "").strip()
    if country and country.lower() not in loc.lower():
        loc = f"{country} ({loc})" if re.fullmatch(r"\d+ locations?", loc, re.I) or not loc else f"{loc}, {country}"
    return Job(
        source="workday", board=o["tenant"], company=target.name, native_id=path,
        title=(p.get("title") or "").strip(), url=f"{base_url(o)}/en-US/{o['site']}{path}",
        location=loc, posted_at=date_from_workday(p.get("postedOn")),
        needs_detail=True, detail={"url": api_url(o) + path, "country": country},
    )


async def _collect(http, target, body: dict, jobs: dict, *, country: str, max_pages: int) -> bool:
    """Fetch pages into jobs. True when the whole result set was read (not cut off by max_pages)."""
    total = None
    offset = 0
    for _ in range(max_pages):
        data = await http.post_json(api_url(target.opts) + "/jobs", {**body, "limit": PAGE, "offset": offset})
        posts = data.get("jobPostings") or []
        if total is None:  # Workday only reports the real total on the first page
            total = data.get("total") or 0
        for p in posts:
            job = parse_posting(p, target, country)
            jobs.setdefault(job.native_id, job)
        offset += PAGE
        if not posts or offset >= total:
            return True
    return False


async def fetch(http, target) -> list[Job]:
    api = api_url(target.opts)
    if not await http.robots_allowed(api + "/jobs"):
        raise Blocked(f"robots.txt disallows {api}")
    first = await http.post_json(api + "/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
    country = country_name(target.opts.get("country", "India"))
    facet = find_country_facet(first.get("facets"), country) if country else None
    jobs: dict[str, Job] = {}
    if facet:
        param, value_id = facet
        whole = await _collect(http, target, {"appliedFacets": {param: [value_id]}, "searchText": ""}, jobs,
                               country=country, max_pages=MAX_PAGES_COUNTRY)
        target.opts["_partial"] = not whole  # a cut-off listing can't tell us which jobs closed
    else:  # no country facet on this tenant: search a few role keywords, filter location later
        for query in target.opts.get("queries") or FALLBACK_QUERIES:
            await _collect(http, target, {"appliedFacets": {}, "searchText": query}, jobs, country="", max_pages=10)
        target.opts["_partial"] = True  # keyword searches never cover the whole board
    return list(jobs.values())


async def enrich(http, job: Job) -> None:
    data = await http.get_json(job.detail["url"], robots=True)
    info = data.get("jobPostingInfo") or {}
    job.description = html_to_text(info.get("jobDescription"))
    locs = [x for x in [info.get("location"), *(info.get("additionalLocations") or [])] if x]
    if locs:
        loc = " / ".join(dict.fromkeys(locs))
        country = job.detail.get("country") or ("India" if job.detail.get("india") else "")
        if country and country.lower() not in loc.lower():
            loc += f", {country}"
        job.location = loc
    job.employment_type = info.get("timeType") or job.employment_type
    job.url = info.get("externalUrl") or job.url
    job.posted_at = date_from_iso(info.get("startDate")) or job.posted_at
