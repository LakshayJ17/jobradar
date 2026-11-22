"""Atlassian careers listing (one public JSON document with every open role; robots.txt allows it)."""

from __future__ import annotations

from datetime import datetime

from ..http import Blocked
from ..models import Job
from ..util import html_to_text

COMPLETE_LISTING = True
API = "https://www.atlassian.com/endpoint/careers/listings"


def _date(value: str | None) -> str | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d %I:%M %p").date().isoformat()
    except ValueError:
        return (value or "")[:10] or None


def parse(data: list) -> list[Job]:
    jobs = []
    for j in data or []:
        locs = [" ".join(str(x).split()) for x in j.get("locations") or []]
        loc = " / ".join(dict.fromkeys(locs))
        parts = [html_to_text(j.get(k)) for k in ("overview", "responsibilities", "qualifications")]
        jobs.append(Job(
            source="atlassian", board="atlassian", company="Atlassian", native_id=str(j.get("id")),
            title=(j.get("title") or "").strip(), url=f"https://www.atlassian.com/company/careers/details/{j.get('id')}",
            location=loc, remote=True if "remote" in loc.lower() else None,
            posted_at=_date((j.get("portalJobPost") or {}).get("updatedDate")),
            description="\n\n".join(p for p in parts if p), department=j.get("category") or "",
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    if not await http.robots_allowed(API):
        raise Blocked(f"robots.txt disallows {API}")
    return parse(await http.get_json(API))
