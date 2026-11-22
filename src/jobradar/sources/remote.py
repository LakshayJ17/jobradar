"""Remote-job boards with free public APIs: Himalayas, RemoteOK, Jobicy. Their terms ask for credit - every job
links back to the board's own page. Only roles open to you (profile locations.remote_open_to) survive the location filter."""

from __future__ import annotations

from ..models import Job
from ..salary import to_lpa
from ..util import country_name, date_from_epoch, date_from_iso, html_to_text

COMPLETE_LISTING = False
_SENIOR = {"senior", "lead", "manager", "director", "executive", "principal"}


def _level(levels) -> str:
    names = {str(x).lower() for x in (levels if isinstance(levels, list) else [levels]) if x}
    if any("entry" in n or "junior" in n or "intern" in n for n in names):
        return "entry_level"
    if names and all(any(s in n for s in _SENIOR) or "mid" in n for n in names):
        return "mid_senior_level"
    return ""


def parse_himalayas(rows: list) -> list[Job]:
    jobs = []
    for r in rows or []:
        where = r.get("locationRestrictions") or []
        jobs.append(Job(
            source="remote", board="himalayas", company=(r.get("companyName") or "").strip(),
            native_id=r.get("guid") or r.get("applicationLink") or r.get("title"), title=(r.get("title") or "").strip(),
            url=r.get("guid") or r.get("applicationLink") or "", remote=True,
            location="Remote" + (f" ({', '.join(where)})" if where else " (anywhere)"),
            posted_at=date_from_epoch(r.get("pubDate")), expires=date_from_epoch(r.get("expiryDate")),
            description=html_to_text(r.get("description") or r.get("excerpt")),
            employment_type=r.get("employmentType") or "", level_hint=_level(r.get("seniority")),
            salary=to_lpa(r.get("minSalary"), r.get("maxSalary"), r.get("currency") or "USD"),
        ))
    return jobs


def parse_remoteok(rows: list) -> list[Job]:
    jobs = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("id") or not r.get("position"):
            continue  # first element is the API's legal notice
        where = (r.get("location") or "").strip()
        jobs.append(Job(
            source="remote", board="remoteok", company=(r.get("company") or "").strip(), native_id=str(r["id"]),
            title=r["position"].strip(), url=r.get("url") or f"https://remoteok.com/remote-jobs/{r['id']}",
            remote=True, location="Remote" + (f" ({where})" if where else " (anywhere)"),
            posted_at=date_from_iso(r.get("date")), description=html_to_text(r.get("description")),
            salary=to_lpa(r.get("salary_min"), r.get("salary_max"), "USD"),
        ))
    return jobs


def parse_jobicy(rows: list) -> list[Job]:
    jobs = []
    for r in rows or []:
        jobs.append(Job(
            source="remote", board="jobicy", company=(r.get("companyName") or "").strip(), native_id=str(r.get("id")),
            title=(r.get("jobTitle") or "").strip(), url=r.get("url") or "", remote=True,
            location=f"Remote ({r.get('jobGeo') or 'anywhere'})", posted_at=date_from_iso(r.get("pubDate")),
            description=html_to_text(r.get("jobDescription") or r.get("jobExcerpt")),
            employment_type=", ".join(r["jobType"]) if isinstance(r.get("jobType"), list) else str(r.get("jobType") or ""),
            level_hint=_level(r.get("jobLevel")),
            salary=to_lpa(r.get("annualSalaryMin"), r.get("annualSalaryMax"), r.get("salaryCurrency") or "USD"),
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    board = target.board
    if board == "himalayas":
        jobs, cursor = [], None
        for _ in range(target.opts.get("pages", 5)):
            params = {"limit": 20, **({"cursor": cursor} if cursor else {})}
            data = await http.get_json("https://himalayas.app/jobs/api", params=params, robots=True)
            jobs += parse_himalayas(data.get("jobs"))
            cursor = data.get("nextCursor")
            if not cursor:
                break
        return jobs
    if board == "remoteok":
        return parse_remoteok(await http.get_json("https://remoteok.com/api", robots=True))
    if board == "jobicy":
        data = await http.get_json("https://jobicy.com/api/v2/remote-jobs", robots=True,
                                   params={"count": 50, "industry": "dev", **_jobicy_geo(target.opts)})
        return parse_jobicy(data.get("jobs"))
    raise ValueError(f"unknown remote board {board}")


_JOBICY_GEO = {"India": "apac", "Singapore": "apac", "Australia": "apac", "New Zealand": "apac", "Japan": "apac",
               "Philippines": "apac", "Malaysia": "apac", "Indonesia": "apac", "Vietnam": "apac",
               "United States": "usa", "Canada": "canada", "United Kingdom": "uk", "Germany": "europe",
               "France": "europe", "Netherlands": "europe", "Spain": "europe", "Poland": "europe", "Ireland": "europe"}


def _jobicy_geo(opts: dict) -> dict:
    """Jobicy's region filter for your country (sources.yaml `geo:` wins); no filter when it has none."""
    geo = opts.get("geo") or _JOBICY_GEO.get(country_name(opts.get("country", "India")))
    return {"geo": geo} if geo else {}
