"""SmartRecruiters public Posting API: https://developers.smartrecruiters.com/docs/posting-api"""

from __future__ import annotations

from ..models import Job
from ..util import country_code, date_from_iso, html_to_text, with_country

API = "https://api.smartrecruiters.com/v1/companies/{board}/postings"
PAGE = 100
MAX_JOBS = 1000


def parse(content: list, board: str, name: str) -> list[Job]:
    jobs = []
    for j in content or []:
        loc_obj = j.get("location") or {}
        loc = ", ".join(x for x in (loc_obj.get("city"), loc_obj.get("region")) if x)
        loc = with_country(loc, (loc_obj.get("country") or "").upper())
        pid = str(j.get("id"))
        jobs.append(Job(
            source="smartrecruiters", board=board, company=name, native_id=pid,
            title=(j.get("name") or "").strip(), url=f"https://jobs.smartrecruiters.com/{board}/{pid}",
            location=loc, remote=True if loc_obj.get("remote") else None,
            posted_at=date_from_iso(j.get("releasedDate")),
            employment_type=(j.get("typeOfEmployment") or {}).get("label") or "",
            department=(j.get("department") or {}).get("label") or "",
            level_hint=(j.get("experienceLevel") or {}).get("id") or "",
            needs_detail=True, detail={"url": f"{API.format(board=board)}/{pid}"},
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    jobs: list[Job] = []
    offset = 0
    code = country_code(target.opts.get("country", "India")).lower()
    country = {"country": code} if code else {}  # unknown country: every posting, the location filter decides
    while offset < MAX_JOBS:
        data = await http.get_json(API.format(board=target.board),
                                   params={**country, "limit": PAGE, "offset": offset})
        content = data.get("content") or []
        jobs += parse(content, target.board, target.name)
        offset += PAGE
        if not content or offset >= (data.get("totalFound") or 0):
            break
    return jobs


async def enrich(http, job: Job) -> None:
    data = await http.get_json(job.detail["url"])
    sections = ((data.get("jobAd") or {}).get("sections")) or {}
    parts = []
    for key in ("jobDescription", "qualifications", "additionalInformation"):
        sec = sections.get(key) or {}
        if sec.get("text"):
            parts.append(f"{sec.get('title') or ''}\n{html_to_text(sec['text'])}".strip())
    job.description = "\n\n".join(parts)
    job.url = data.get("postingUrl") or job.url
