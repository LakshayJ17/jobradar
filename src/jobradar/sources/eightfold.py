"""Eightfold-powered career sites (Microsoft, Qualcomm, ...). Their robots.txt explicitly allows /api/pcsx."""

from __future__ import annotations

from ..http import Blocked
from ..models import Job
from ..util import country_name, date_from_epoch, html_to_text

COMPLETE_LISTING = False  # keyword searches, a few pages each - not the whole board
MAX_PAGES = 8  # results are sorted newest first; 8 pages x 10 per query is plenty for a 30-day window
DEFAULT_QUERIES = ("software engineer", "machine learning")


def parse_position(p: dict, target) -> Job:
    host = target.opts["host"]
    pid = str(p.get("id"))
    path = p.get("positionUrl") or f"/careers/job/{pid}"
    locs = p.get("locations") or p.get("standardizedLocations") or []
    return Job(
        source="eightfold", board=target.board, company=target.name, native_id=pid,
        title=(p.get("name") or "").strip(), url=path if path.startswith("http") else f"https://{host}{path}",
        location=" / ".join(dict.fromkeys(locs)),
        remote=True if "remote" in (p.get("workLocationOption") or "").lower() else None,
        posted_at=date_from_epoch(p.get("postedTs") or p.get("creationTs")),
        department=p.get("department") or "",
        needs_detail=True,
        detail={"url": f"https://{host}/api/pcsx/position_details",
                "params": {"position_id": pid, "domain": target.opts["domain"], "hl": "en"}},
    )


async def fetch(http, target) -> list[Job]:
    api = f"https://{target.opts['host']}/api/pcsx/search"
    if not await http.robots_allowed(api):
        raise Blocked(f"robots.txt disallows {api}")
    seen: dict[str, Job] = {}
    queries = list(target.opts.get("queries") or DEFAULT_QUERIES)
    if target.opts.get("internships"):
        queries.append("intern")
    for query in dict.fromkeys(queries):
        start = 0
        for _ in range(MAX_PAGES):
            data = await http.get_json(api, params={"domain": target.opts["domain"], "query": query,
                                                    "location": country_name(target.opts.get("country", "India")),
                                                    "start": start, "sort_by": "timestamp"})
            d = data.get("data") or {}
            positions = d.get("positions") or []
            for p in positions:
                job = parse_position(p, target)
                seen.setdefault(job.native_id, job)
            start += len(positions)
            if not positions or start >= (d.get("count") or 0):
                break
    return list(seen.values())


async def enrich(http, job: Job) -> None:
    data = await http.get_json(job.detail["url"], params=job.detail["params"], robots=True)
    d = data.get("data") or {}
    job.description = html_to_text(d.get("jobDescription"))
    job.url = d.get("publicUrl") or job.url
