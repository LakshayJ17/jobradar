"""Ashby public Job Posting API: https://developers.ashbyhq.com/docs/public-job-posting-api"""

from __future__ import annotations

from ..models import Job
from ..util import date_from_iso, html_to_text, with_country

API = "https://api.ashbyhq.com/posting-api/job-board/{board}"


def _country(entry: dict) -> str:
    return (((entry or {}).get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""


def parse(data: dict, board: str, name: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs") or []:
        if j.get("isListed") is False:
            continue
        secondary = j.get("secondaryLocations") or []
        locs = [j.get("location") or ""] + [s.get("location") or "" for s in secondary]
        loc = " / ".join(dict.fromkeys(x for x in locs if x))
        countries = {_country(j)} | {_country(s) for s in secondary}
        for country in sorted(c for c in countries if c):
            loc = with_country(loc, country)
        jobs.append(Job(
            source="ashby", board=board, company=name, native_id=str(j.get("id")),
            title=(j.get("title") or "").strip(), url=j.get("jobUrl") or "",
            location=loc,
            remote=True if j.get("isRemote") or (j.get("workplaceType") or "").lower() == "remote" else None,
            posted_at=date_from_iso(j.get("publishedAt")),
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
            employment_type=j.get("employmentType") or "",
            department=j.get("department") or j.get("team") or "",
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    data = await http.get_json(API.format(board=target.board), params={"includeCompensation": "false"})
    return parse(data, target.board, target.name)
