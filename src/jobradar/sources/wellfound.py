"""Wellfound (startup jobs) - scraped role pages, e.g. wellfound.com/role/l/software-engineer/india.
robots.txt allows these pages and the terms have no anti-scraping clause (checked 2026-11-17). The site uses a
Cloudflare check: if it ever challenges us, http.py stops - we never try to get around it."""

from __future__ import annotations

import json
import re

from ..http import FetchError
from ..models import Job
from ..salary import parse_text
from ..util import country_name, date_from_epoch, tag_blocks

COMPLETE_LISTING = False
BASE = "https://wellfound.com"
DEFAULT_ROLES = ("software-engineer", "full-stack-engineer", "backend-engineer", "machine-learning-engineer",
                 "data-scientist")
_NEXT_OPEN = re.compile(r'<script id="__NEXT_DATA__"[^>]{0,400}>')


def parse_page(page: str) -> list[Job]:
    m = next((inner for _, inner in tag_blocks(page, _NEXT_OPEN, "</script>")), None)
    if not m:
        raise FetchError("Wellfound page has no embedded data (layout changed or blocked)")
    state = (((json.loads(m).get("props") or {}).get("pageProps") or {}).get("apolloState") or {}).get("data") or {}
    jobs = []
    for key, startup in state.items():
        if not key.startswith("Startup") or not isinstance(startup, dict):
            continue
        for ref in startup.get("highlightedJobListings") or []:
            jl = state.get((ref or {}).get("__ref", ""), {})
            if not jl.get("id"):
                continue
            locs = list(jl.get("locationNames") or [])
            if jl.get("remote"):
                accepted = jl.get("acceptedRemoteLocationNames") or []
                locs.append("Remote" + (f" ({', '.join(accepted)})" if accepted else ""))
            parts = []
            if jl.get("yearsExperienceMin") is not None:
                parts.append(f"Experience required: {jl['yearsExperienceMin']} years of experience.")
            if startup.get("highConcept"):
                parts.append(f"{startup.get('name')}: {startup['highConcept']}")
            parts.append(jl.get("description") or "")
            jobs.append(Job(
                source="wellfound", board="wellfound", company=(startup.get("name") or "").strip(),
                native_id=str(jl["id"]), title=(jl.get("title") or "").strip(),
                url=f"{BASE}/jobs/{jl['id']}-{jl.get('slug') or 'job'}",
                location=" / ".join(dict.fromkeys(x for x in locs if x)), remote=True if jl.get("remote") else None,
                posted_at=date_from_epoch(jl.get("liveStartAt")), description="\n\n".join(p for p in parts if p),
                employment_type=jl.get("jobType") or "",
                level_hint="entry_level" if jl.get("yearsExperienceMin") == 0 else "",
                salary=parse_text(str(jl.get("compensation") or "").split("•")[0]),
            ))
    return jobs


async def fetch(http, target) -> list[Job]:
    jobs: dict[str, Job] = {}
    for role in target.opts.get("roles") or DEFAULT_ROLES:
        where = target.opts.get("location") or re.sub(r"\W+", "-", country_name(target.opts.get("country", "India")).lower())
        url = f"{BASE}/role/l/{role}/{where}" if where else f"{BASE}/role/{role}"
        for job in parse_page(await http.get_html(url)):
            jobs.setdefault(job.native_id, job)
    return list(jobs.values())
