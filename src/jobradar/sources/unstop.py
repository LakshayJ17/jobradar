"""Unstop (India; lots of fresher hiring). Uses its public opportunity search API (robots.txt allows /api/public)."""

from __future__ import annotations

from ..models import Job
from ..salary import to_lpa
from ..util import date_from_iso, html_to_text

COMPLETE_LISTING = False
API = "https://unstop.com/api/public/opportunity/search-result"
PER_PAGE = 30
MAX_PAGES = 3
DEFAULT_QUERIES = ("software", "developer", "engineer", "data", "machine learning", "ai")


def parse(rows: list) -> list[Job]:
    jobs = []
    for r in rows or []:
        detail = r.get("jobDetail") or {}
        eligible = {f.get("name") for f in r.get("filters") or [] if f.get("type") == "eligible"}
        cities = [x.get("city") or "" for x in r.get("locations") or []] or list(detail.get("locations") or [])
        countries = {x.get("country") for x in r.get("locations") or [] if x.get("country")}
        remote = (detail.get("type") or "").lower() in ("wfh", "work_from_home", "remote")
        loc = ", ".join(dict.fromkeys(c for c in cities if c))
        if not countries or "India" in countries:
            loc = f"{loc}, India" if loc else "India"
        if remote:
            loc = f"Remote, {loc}"
        parts = []
        if detail.get("min_experience") not in (None, ""):
            parts.append(f"Experience required: {detail['min_experience']} years of experience.")
        parts.append(html_to_text(r.get("details")))
        skills = [s.get("skill") or s.get("name") for s in r.get("required_skills") or [] if isinstance(s, dict)]
        if skills:
            parts.append("Skills: " + ", ".join(s for s in skills if s))
        if "Fresher" in eligible:
            level = "fresher"
        elif eligible == {"Experienced Professionals"}:
            level = "mid_senior_level"
        else:
            level = ""
        jobs.append(Job(
            source="unstop", board="unstop", company=((r.get("organisation") or {}).get("name") or "").strip() or "Unstop",
            native_id=str(r.get("id")), title=(r.get("title") or "").strip(),
            url="https://unstop.com/" + (r.get("public_url") or f"o/{r.get('id')}").lstrip("/"),
            location=loc, remote=True if remote else None,
            posted_at=date_from_iso((r.get("approved_date") or r.get("updated_at") or "")[:10]),
            expires=date_from_iso(str((r.get("regnRequirements") or {}).get("end_regn_dt") or r.get("end_date") or "")[:10]),
            accepting=_accepting(r),
            description="\n\n".join(p for p in parts if p),
            employment_type=detail.get("timing") or "", level_hint=level, salary=_salary(detail),
        ))
    return jobs


def _accepting(r: dict) -> bool | None:
    """Unstop says whether registrations are open: regn_open 1/0 and status LIVE / EXPIRED / CLOSED."""
    flags = [r.get("regn_open"), r.get("status"), (r.get("regnRequirements") or {}).get("reg_status")]
    if all(f is None for f in flags):
        return None
    status = str(r.get("status") or "").upper()
    reg = str((r.get("regnRequirements") or {}).get("reg_status") or "").upper()
    return not (str(r.get("regn_open")) == "0" or status in ("EXPIRED", "CLOSED", "FINISHED", "ENDED")
                or reg in ("ENDED", "CLOSED", "EXPIRED"))


def _salary(detail: dict) -> list[float] | None:
    if str(detail.get("show_salary")) not in ("1", "True", "true"):
        return None  # the employer chose not to show pay
    currency = "USD" if "dollar" in str(detail.get("currency") or "") else "INR"
    period = "month" if "month" in str(detail.get("pay_in") or "").lower() else "year"
    try:
        return to_lpa(float(detail.get("min_salary") or 0), float(detail.get("max_salary") or 0), currency, period)
    except (TypeError, ValueError):
        return None


async def fetch(http, target) -> list[Job]:
    jobs: dict[str, Job] = {}
    for query in target.opts.get("queries") or DEFAULT_QUERIES:
        for page in range(1, MAX_PAGES + 1):
            data = await http.get_json(API, robots=True, params={
                "opportunity": "jobs", "page": page, "per_page": PER_PAGE, "oppstatus": "open", "searchTerm": query})
            block = data.get("data") or {}
            batch = parse(block.get("data"))
            for job in batch:
                jobs.setdefault(job.native_id, job)
            if not batch or page >= (block.get("last_page") or 1):
                break
    return list(jobs.values())
