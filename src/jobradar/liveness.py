"""Is a tracked posting still open? A link that loads isn't enough: pages stay up after a job stops taking
applications ("Applications are closed for this job"), get taken down (HTTP 410), or quietly redirect to the job list.

Checks, per source, based on what each site actually does when a job closes:

* job pages (Internshala, Shine, Wellfound, Google, Amazon, remote boards, company boards): gone (404 / 410), the page
  says applications are closed or the job was filled / expired / taken down, the link now redirects away from the
  job, or its schema.org JobPosting deadline (validThrough) has passed. A page that still carries the JobPosting is
  open.
* Workday: the career page drops its JobPosting data when a job is unposted; for one still posted, Workday's own API
  says whether it can still be applied to (canApply).
* Microsoft and other Eightfold sites: their position API answers 404 for a closed job.
* Oracle career sites: their requisition API returns nothing for a closed job, or a past end date.
* Unstop: its search results say whether registrations are open; the daily run reads that, so nothing is fetched here.

Everything goes through the same polite, robots.txt-checked client as the daily run. Anything that can't be told
for sure is "unknown" and the job stays as it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .http import Blocked, FetchError, Http
from .sources import build_targets, complete_listing
from .sources.jsonld import job_postings
from .store import Store
from .util import date_from_iso, html_to_text, today

CLOSED_TEXT = re.compile(
    r"applications? (?:are|is) (?:now )?closed|no longer (?:accepting|available for|open for|taking) (?:any )?applications|"
    r"(?:this|the) (?:job|position|role|posting|opening|internship|listing|vacancy|requisition)(?: listing)? "
    r"(?:is |has been |was )?(?:recently )?(?:closed|filled|expired|taken down|no longer (?:available|active|open|accepting))|"
    r"job (?:has )?expired|position (?:has been )?filled|registrations? (?:are |is )?closed|"
    r"not accepting (?:any more |new )?applications|job (?:is )?no longer available|"
    r"(?:job|page) (?:you(?:'re| are) looking for )?(?:could not be|cannot be|can't be|was not) found|job not found", re.I)
OPEN, CLOSED, UNKNOWN = "open", "closed", "unknown"


@dataclass
class Verdict:
    state: str   # open / closed / unknown
    reason: str


def _job_token(url: str) -> str:
    """The part of a job URL that identifies the job: its longest number (5+ digits), else its last path segment."""
    path = urlsplit(url).path
    numbers = re.findall(r"\d{5,}", path)
    if numbers:
        return max(numbers, key=len)
    last = path.rstrip("/").rsplit("/", 1)[-1]
    return last if len(last) >= 8 else ""


def _redirected_away(url: str, final: str) -> bool:
    token = _job_token(url)
    return bool(token) and token.lower() not in final.lower()


def page_verdict(status: int, url: str, final: str, page: str) -> Verdict:
    """What a job page says about the job (see the module docstring)."""
    if status in (404, 410):
        return Verdict(CLOSED, f"the posting page is gone (HTTP {status})")
    if _redirected_away(url, final):
        return Verdict(CLOSED, "the link now redirects away from the job")
    text = html_to_text(page[:3_000_000]) if "<" in page[:5000] else page
    m = CLOSED_TEXT.search(text[:400_000])
    if m:
        return Verdict(CLOSED, f'the page says "{" ".join(m.group(0).split())}"')
    postings = job_postings(page)
    if postings:
        deadline = date_from_iso(str(postings[0].get("validThrough") or ""))
        if deadline and deadline < today().isoformat():
            return Verdict(CLOSED, f"its application deadline ({deadline}) has passed")
        return Verdict(OPEN, "the job page still lists it")
    return Verdict(UNKNOWN, "the page doesn't say either way")


# Sites seen to say so plainly when a job closes (404 / 410, "Applications are closed", "Job not found"), so a page
# that loads and says none of that is open even without JobPosting data (Amazon, Google and Internshala pages often
# lack it). Elsewhere (single-page apps), such a page stays "unknown".
SAYS_WHEN_CLOSED = {"amazon", "google", "scrape", "wellfound", "remote"}


async def _page(http: Http, url: str, source: str = "") -> Verdict:
    r = await http.get_page(url)
    verdict = page_verdict(r.status_code, url, str(r.request.url), r.text)
    if verdict.state == UNKNOWN and r.status_code == 200 and source in SAYS_WHEN_CLOSED:
        return Verdict(OPEN, "the job page is up and doesn't say it's closed")
    return verdict


async def check(http: Http, rec: dict, targets: dict) -> Verdict:
    """Is this tracked job still open? Never raises for ordinary failures: they are "unknown"."""
    if rec.get("expires") and rec["expires"] < today().isoformat():
        return Verdict(CLOSED, f"its closing date ({rec['expires']}) has passed")
    source, native = rec.get("source"), str(rec.get("key", "")).split(":", 2)[-1]
    target = targets.get((source, rec.get("board")))
    try:
        if source == "workday":
            r = await http.get_page(rec["url"])
            first = page_verdict(r.status_code, rec["url"], str(r.request.url), r.text)
            if first.state == CLOSED:
                return first
            if not job_postings(r.text):
                return Verdict(CLOSED, "the career site no longer posts it")
            if target:
                from .sources.workday import api_url

                api = await http.get_page(api_url(target.opts) + native, accept="application/json")
                info = (api.json().get("jobPostingInfo") or {}) if api.status_code == 200 else {}
                if info.get("canApply") is False:
                    return Verdict(CLOSED, "still posted, but no longer taking applications")
            return Verdict(OPEN, "the career site still lists it")
        if source == "eightfold" and target:
            host = urlsplit(rec["url"]).netloc
            r = await http.get_page(f"https://{host}/api/pcsx/position_details", accept="application/json",
                                    params={"position_id": native, "domain": target.opts.get("domain"), "hl": "en"})
            if r.status_code in (404, 410):
                return Verdict(CLOSED, "the career site no longer has it")
            return Verdict(OPEN, "the career site still lists it") if (r.json() or {}).get("data") else \
                Verdict(UNKNOWN, "the career site's answer was empty")
        if source == "oracle" and target:
            from .sources.oracle import _api

            o = target.opts
            r = await http.get_page(_api(o, "recruitingCEJobRequisitionDetails"), accept="application/json",
                                    params={"onlyData": "true", "expand": "all",
                                            "finder": f'ById;Id="{native}",siteNumber={o["site"]}'})
            items = (r.json().get("items") or []) if r.status_code == 200 else []
            if r.status_code in (404, 410) or (r.status_code == 200 and not items):
                return Verdict(CLOSED, "the career site no longer has it")
            end = date_from_iso(str((items[0] if items else {}).get("ExternalPostedEndDate") or ""))
            if end and end < today().isoformat():
                return Verdict(CLOSED, f"its posting ended on {end}")
            return Verdict(OPEN, "the career site still lists it") if items else Verdict(UNKNOWN, "no answer")
        if source == "unstop":
            return Verdict(UNKNOWN, "Unstop's daily search says when registrations close")
        return await _page(http, rec["url"], source)
    except Blocked as exc:
        return Verdict(UNKNOWN, f"not checked: {exc}")
    except (FetchError, ValueError, KeyError) as exc:
        return Verdict(UNKNOWN, f"couldn't check: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - one odd page must not stop the run
        return Verdict(UNKNOWN, f"couldn't check: {type(exc).__name__}")


def record(store: Store, rec: dict, verdict: Verdict) -> None:
    rec["verified"] = {"on": today().isoformat(), "state": verdict.state, "reason": verdict.reason}
    if verdict.state == CLOSED and not rec.get("closed"):
        store.close(rec, verdict.reason)


def due(store: Store, *, every_days: int, limit: int) -> list[dict]:
    """Jobs to re-check this run: open, not dismissed or applied to yet, from sources whose daily listing can't prove
    a job is still open, not checked in the last every_days days, not first seen today. Best-ranked first."""
    from datetime import timedelta

    cutoff = (today() - timedelta(days=every_days)).isoformat()
    now = today().isoformat()
    picks = []
    for rec in store.all():
        if rec.get("closed") or rec.get("status") not in ("new", "saved") or not store.visible(rec):
            continue
        if rec.get("first_seen") == now or (rec.get("verified") or {}).get("on", "") > cutoff:
            continue
        if rec["source"] == "unstop":
            continue  # its daily search says when registrations close
        if complete_listing(rec["source"]) and rec["source"] not in ("smartrecruiters", "workday"):
            continue  # its board is read in full every day: a closed job disappears from it
        picks.append(rec)
    never = [r for r in picks if not r.get("verified")]
    again = sorted((r for r in picks if r.get("verified")), key=lambda r: r["verified"]["on"])
    return (store.ranked(never) + again)[:limit]


async def verify(store: Store, sources_cfg: dict, recs: list[dict], *, log=print) -> dict:
    """Check recs and record the results. Returns counts."""
    import asyncio

    targets = {(t.source, t.board): t for t in build_targets(sources_cfg)}
    counts = {"checked": 0, OPEN: 0, CLOSED: 0, UNKNOWN: 0, "closed_jobs": []}
    if not recs:
        return counts
    async with Http() as http:
        verdicts = await asyncio.gather(*(check(http, r, targets) for r in recs))
    for rec, verdict in zip(recs, verdicts):
        record(store, rec, verdict)
        counts["checked"] += 1
        counts[verdict.state] += 1
        if verdict.state == CLOSED:
            counts["closed_jobs"].append((rec["id"], rec["company"], rec["title"], verdict.reason))
    return counts
