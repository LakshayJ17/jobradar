"""Greenhouse public Job Board API: https://developers.greenhouse.io/job-board.html"""

from __future__ import annotations

from ..models import Job
from ..util import date_from_iso, html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs"


def parse(data: dict, board: str, name: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs") or []:
        loc = ((j.get("location") or {}).get("name") or "").strip()
        offices = ", ".join(o["name"] for o in j.get("offices") or [] if o.get("name"))
        if offices and offices.lower() not in loc.lower():
            loc = f"{loc} ({offices})" if loc else offices
        jobs.append(Job(
            source="greenhouse", board=board, company=name, native_id=str(j.get("id")),
            title=(j.get("title") or "").strip(), url=j.get("absolute_url") or "",
            location=loc, remote=True if "remote" in loc.lower() else None,
            posted_at=date_from_iso(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j.get("content")),
            department=", ".join(d["name"] for d in j.get("departments") or [] if d.get("name")),
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    data = await http.get_json(API.format(board=target.board), params={"content": "true"})
    return parse(data, target.board, target.name)
