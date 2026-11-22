"""Oracle Recruiting Cloud career sites (JPMorgan Chase, Oracle, ...): the public REST API their candidate
sites use, after robots.txt allows it. Your country is found through the site's own location facet."""

from __future__ import annotations

from ..http import Blocked
from ..models import Job
from ..util import country_names, date_from_iso, html_to_text, with_country

COMPLETE_LISTING = False  # keyword searches, newest first - not the full board
PAGE = 25
MAX_PAGES = 4
DEFAULT_KEYWORDS = ("software", "engineer", "developer", "data", "machine learning", "graduate")


def _api(o: dict, resource: str) -> str:
    return f"https://{o['host']}/hcmRestApi/resources/latest/{resource}"


def _finder(o: dict, **kw) -> str:
    args = [f"siteNumber={o['site']}"] + [f"{k}={v}" for k, v in kw.items() if v not in (None, "")]
    return "findReqs;" + ",".join(args)


def find_country(items: list, country: str = "India"):
    names = set(country_names(country))
    for item in items or []:
        for loc in item.get("locationsFacet") or []:
            if names and (loc.get("Name") or "").strip().lower() in names:
                return loc.get("Id")
    return None


def parse_requisitions(items: list, target) -> list[Job]:
    o = target.opts
    jobs = []
    for item in items or []:
        for r in item.get("requisitionList") or []:
            rid = str(r.get("Id"))
            locs = [r.get("PrimaryLocation") or ""] + [s.get("Name") or "" for s in r.get("secondaryLocations") or []]
            loc = " / ".join(dict.fromkeys(x for x in locs if x))
            loc = with_country(loc, r.get("PrimaryLocationCountry"))
            jobs.append(Job(
                source="oracle", board=target.board, company=target.name, native_id=rid,
                title=(r.get("Title") or "").strip(),
                url=f"https://{o['host']}/hcmUI/CandidateExperience/en/sites/{o['site']}/job/{rid}",
                location=loc, posted_at=date_from_iso(r.get("PostedDate")),
                remote=True if "remote" in (r.get("WorkplaceType") or "").lower() else None,
                department=r.get("JobFamily") or "", description=html_to_text(r.get("ShortDescriptionStr")),
                needs_detail=True,
                detail={"url": _api(o, "recruitingCEJobRequisitionDetails"),
                        "params": {"expand": "all", "onlyData": "true",
                                   "finder": f'ById;Id="{rid}",siteNumber={o["site"]}'}},
            ))
    return jobs


async def fetch(http, target) -> list[Job]:
    o = target.opts
    url = _api(o, "recruitingCEJobRequisitions")
    if not await http.robots_allowed(url):
        raise Blocked(f"robots.txt disallows {url}")
    base = {"onlyData": "true", "expand": "requisitionList.secondaryLocations"}
    facets = await http.get_json(url, params={**base, "finder": _finder(o, facetsList="LOCATIONS", limit=1)})
    country_id = find_country(facets.get("items"), o.get("country", "India")) if o.get("country", "India") else None
    jobs: dict[str, Job] = {}
    for keyword in o.get("keywords") or DEFAULT_KEYWORDS:
        for page in range(MAX_PAGES):
            data = await http.get_json(url, params={**base, "finder": _finder(
                o, limit=PAGE, offset=page * PAGE, keyword=keyword, locationId=country_id, sortBy="POSTING_DATES_DESC")})
            batch = parse_requisitions(data.get("items"), target)
            for job in batch:
                jobs.setdefault(job.native_id, job)
            total = ((data.get("items") or [{}])[0]).get("TotalJobsCount") or 0
            if len(batch) < PAGE or (page + 1) * PAGE >= total:
                break
    return list(jobs.values())


async def enrich(http, job: Job) -> None:
    data = await http.get_json(job.detail["url"], params=job.detail["params"])
    d = (data.get("items") or [{}])[0]
    parts = [html_to_text(d.get(k)) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                               "ExternalQualificationsStr")]
    job.description = "\n\n".join(p for p in parts if p) or job.description
