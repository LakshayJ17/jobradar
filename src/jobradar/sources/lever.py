"""Lever public Postings API: https://github.com/lever/postings-api"""

from __future__ import annotations

from ..models import Job
from ..salary import to_lpa
from ..util import date_from_epoch, html_to_text, with_country

API = "https://api.lever.co/v0/postings/{board}"


def _salary(rng) -> list[float] | None:
    if not isinstance(rng, dict):
        return None
    interval = str(rng.get("interval") or "")
    period = "hour" if "hour" in interval else "month" if "month" in interval else "year"
    return to_lpa(rng.get("min"), rng.get("max"), rng.get("currency") or "INR", period)


def parse(data: list, board: str, name: str) -> list[Job]:
    jobs = []
    for j in data or []:
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or ([cat["location"]] if cat.get("location") else [])
        loc = " / ".join(dict.fromkeys(locs))
        loc = with_country(loc, j.get("country"))
        parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for block in j.get("lists") or []:
            parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        jobs.append(Job(
            source="lever", board=board, company=name, native_id=str(j.get("id")),
            title=(j.get("text") or "").strip(), url=j.get("hostedUrl") or "",
            location=loc, remote=True if (j.get("workplaceType") or "").lower() == "remote" else None,
            posted_at=date_from_epoch(j.get("createdAt"), millis=True),
            description="\n\n".join(p.strip() for p in parts if p and p.strip()),
            employment_type=cat.get("commitment") or "", department=cat.get("team") or "",
            salary=_salary(j.get("salaryRange")),
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    data = await http.get_json(API.format(board=target.board), params={"mode": "json"})
    return parse(data, target.board, target.name)
