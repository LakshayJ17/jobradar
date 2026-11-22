"""Workable public job widget API (apply.workable.com). Rate-limits hard, so one request at a time."""

from __future__ import annotations

from ..models import Job
from ..util import date_from_iso, html_to_text

LIST = "https://apply.workable.com/api/v1/widget/accounts/{board}"
DETAIL = "https://apply.workable.com/api/v2/accounts/{board}/jobs/{code}"


def parse(data: dict, board: str, name: str) -> list[Job]:
    jobs = []
    for j in data.get("jobs") or []:
        loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
        code = str(j.get("shortcode") or j.get("id"))
        jobs.append(Job(
            source="workable", board=board, company=name, native_id=code,
            title=(j.get("title") or "").strip(),
            url=j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{board}/j/{code}/",
            location=loc, remote=True if j.get("telecommuting") else None,
            posted_at=date_from_iso(j.get("published_on") or j.get("created_at")),
            employment_type=j.get("employment_type") or "", department=j.get("department") or "",
            needs_detail=True, detail={"url": DETAIL.format(board=board, code=code)},
        ))
    return jobs


async def fetch(http, target) -> list[Job]:
    return parse(await http.get_json(LIST.format(board=target.board)), target.board, target.name)


async def enrich(http, job: Job) -> None:
    data = await http.get_json(job.detail["url"])
    parts = [html_to_text(data.get(k)) for k in ("description", "requirements", "benefits")]
    job.description = "\n\n".join(p for p in parts if p)
