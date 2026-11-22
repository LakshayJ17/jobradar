"""Rippling ATS job boards: the public board API lists every job (title, location, link); the description comes
from the job page's embedded data (ats.rippling.com robots.txt only disallows /internal/)."""

from __future__ import annotations

import json
import re

from ..models import Job
from ..util import date_from_iso, html_to_text

COMPLETE_LISTING = True
API = "https://api.rippling.com/platform/api/ats/v1/board/{board}/jobs"
_NEXT_RX = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def parse(rows: list, board: str, name: str) -> list[Job]:
    jobs = []
    for r in rows or []:
        loc = (r.get("workLocation") or {}).get("label") or ""
        jobs.append(Job(
            source="rippling", board=board, company=name, native_id=str(r.get("uuid")),
            title=(r.get("name") or "").strip(), url=r.get("url") or "", location=loc,
            remote=True if "remote" in loc.lower() else None,
            department=(r.get("department") or {}).get("label") or "",
            needs_detail=True, detail={"url": r.get("url")},
        ))
    return jobs


def parse_job_page(page: str) -> dict:
    m = _NEXT_RX.search(page)
    if not m:
        raise ValueError("no embedded job data on the Rippling page")
    post = ((((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("apiData") or {})
            .get("jobPost") or {})
    desc = post.get("description")
    if isinstance(desc, dict):  # e.g. {"company": "<html>", "role": "<html>"} - role first
        text = "\n\n".join(html_to_text(desc[k]) for k in sorted(desc, key=lambda k: k != "role") if isinstance(desc[k], str))
    else:
        text = html_to_text(desc)
    return {"description": text, "posted_at": date_from_iso(post.get("createdOn") or "")}


async def fetch(http, target) -> list[Job]:
    return parse(await http.get_json(API.format(board=target.board), robots=True), target.board, target.name)


async def enrich(http, job: Job) -> None:
    info = parse_job_page(await http.get_html(job.detail["url"]))
    job.description = info["description"]
    job.posted_at = info["posted_at"] or job.posted_at
