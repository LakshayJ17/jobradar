"""Is a tracked job still open? Pages that stay up after applications close, taken-down jobs, redirects, deadlines,
board flags (Unstop, Workday), the per-run schedule, and the status shown for older jobs."""

import asyncio
from datetime import timedelta

import httpx
import pytest

from jobradar import liveness
from jobradar.filters import Rules
from jobradar.models import Job
from jobradar.sources import Target, unstop
from jobradar.store import Store, status_label
from jobradar.util import today

LD = ('<script type="application/ld+json">{"@type": "JobPosting", "title": "SDE", "validThrough": "%s"}</script>')


@pytest.mark.parametrize("status,final,page,state", [
    (410, None, "<p>The job listing was recently taken down</p>", "closed"),
    (404, None, "<p>Not here</p>", "closed"),
    (200, None, "<h1>SDE - Fresher</h1><p>Applications are closed for this job. Click here to browse more jobs.</p>",
     "closed"),
    (200, None, "<p>Back to jobs search</p><p>Job not found. This job may have been taken down.</p>", "closed"),
    (200, "https://job-boards.greenhouse.io/acme?error=true", "<p>All jobs</p>", "closed"),
    (200, None, LD % "2001-01-01", "closed"),
    (200, None, LD % "2999-01-01", "open"),
    (200, None, "<p>We're hiring. Apply now.</p>", "unknown"),
])
def test_page_verdicts(status, final, page, state):
    url = "https://job-boards.greenhouse.io/acme/jobs/8312377"
    assert liveness.page_verdict(status, url, final or url, page).state == state


def test_open_pages_with_closing_words_elsewhere_are_not_misread():
    page = (LD % "2999-01-01") + "<p>You will build our applications.</p><p>Similar jobs: Data Analyst</p>"
    assert liveness.page_verdict(200, "https://x.example/job/123456", "https://x.example/job/123456", page).state == "open"
    moved = "https://remoteok.com/remote-jobs/remote-ai-reviewer-imerit-113768"
    assert liveness.page_verdict(200, "https://remoteOK.com/remote-jobs/remote-ai-reviewer-imerit-113768", moved,
                                 LD % "2999-01-01").state == "open"


def _rec(store, **kw):
    base = dict(source="scrape", board="internshala", company="Acme", native_id="/job/detail/sde-1111111",
                title="SDE", url="https://internshala.com/job/detail/sde-1111111", location="Pune",
                posted_at=today().isoformat())
    base.update(kw)
    rec = store.add(Job(**base), rule_score=70, signals=[], min_years=None)
    rec["first_seen"] = (today() - timedelta(days=5)).isoformat()
    return rec


def test_check_uses_each_sites_signal(tmp_path, monkeypatch):
    monkeypatch.setattr("jobradar.http.BUCKET_LIMITS", {})  # no politeness spacing against a fake server
    store = Store(tmp_path / "j.json")
    gone = _rec(store, native_id="/job/detail/a-2222222", url="https://internshala.com/job/detail/a-2222222")
    shut = _rec(store, native_id="/job/detail/b-3333333", url="https://internshala.com/job/detail/b-3333333")
    fine = _rec(store, native_id="/job/detail/c-4444444", url="https://internshala.com/job/detail/c-4444444")
    amazon = _rec(store, source="amazon", board="amazon", native_id="10596351",
                  url="https://www.amazon.jobs/en/jobs/10596351/data-engineer")
    workday = _rec(store, source="workday", board="acme", native_id="/job/Pune/SDE_R1",
                   url="https://acme.wd1.myworkdayjobs.com/ext/job/Pune/SDE_R1")
    pages = {
        "/job/detail/a-2222222": (410, "<p>gone</p>"),
        "/job/detail/b-3333333": (200, "<h1>SDE</h1><p>Applications are closed for this job.</p>"),
        "/job/detail/c-4444444": (200, "<h1>SDE</h1><p>Apply by 5 Dec</p>"),
        "/en/jobs/10596351/data-engineer": (200, "<h1>Data Engineer I</h1>"),
        "/ext/job/Pune/SDE_R1": (200, LD % "2999-01-01"),
        "/wday/cxs/acme/ext/job/Pune/SDE_R1": (200, '{"jobPostingInfo": {"canApply": false}}'),
    }

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        status, body = pages[request.url.path]
        return httpx.Response(status, text=body)

    real = liveness.Http
    monkeypatch.setattr(liveness, "Http", lambda **kw: real(transport=httpx.MockTransport(handler)))
    cfg = {"workday": [{"tenant": "acme", "wd": "wd1", "site": "ext", "name": "Acme"}]}
    counts = asyncio.run(liveness.verify(store, cfg, [gone, shut, fine, amazon, workday]))
    assert gone["closed"] and "410" in gone["closed_reason"]
    assert shut["closed"] and "applications are closed" in shut["closed_reason"].lower()
    assert not fine["closed"] and fine["verified"]["state"] == "open"  # internshala page says nothing closed
    assert not amazon["closed"] and amazon["verified"]["state"] == "open"
    assert workday["closed"] and "no longer taking applications" in workday["closed_reason"]
    assert counts["closed"] == 3 and counts["open"] == 2 and not store.visible(gone)


def test_blocked_or_failed_checks_change_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("jobradar.http.BUCKET_LIMITS", {})
    store = Store(tmp_path / "j.json")
    rec = _rec(store)

    def handler(request):
        return httpx.Response(403) if request.url.path != "/robots.txt" else httpx.Response(404)

    real = liveness.Http
    monkeypatch.setattr(liveness, "Http", lambda **kw: real(transport=httpx.MockTransport(handler)))
    asyncio.run(liveness.verify(store, {}, [rec]))
    assert not rec["closed"] and rec["verified"]["state"] == "unknown"


def test_due_picks_the_right_jobs(tmp_path):
    store = Store(tmp_path / "j.json")
    scraped = _rec(store, native_id="/job/1111111")
    applied = _rec(store, native_id="/job/2222222")
    applied["status"] = "applied"
    board = _rec(store, source="greenhouse", board="acme", native_id="1")  # read in full daily: not re-checked
    fresh = _rec(store, native_id="/job/3333333")
    fresh["first_seen"] = today().isoformat()  # fetched today
    recent = _rec(store, native_id="/job/4444444")
    recent["verified"] = {"on": today().isoformat(), "state": "open", "reason": "x"}
    old = _rec(store, native_id="/job/5555555")
    old["verified"] = {"on": (today() - timedelta(days=10)).isoformat(), "state": "open", "reason": "x"}
    un = _rec(store, source="unstop", board="unstop", native_id="77")
    picked = liveness.due(store, every_days=3, limit=10)
    assert [r["id"] for r in picked] == [scraped["id"], old["id"]]
    assert all(r not in picked for r in (applied, board, fresh, recent, un))
    assert len(liveness.due(store, every_days=3, limit=1)) == 1


def test_boards_that_say_applications_closed(tmp_path, make_job, profile):
    row = {"id": 5, "title": "SDE", "organisation": {"name": "Acme"}, "locations": [{"city": "Pune", "country": "India"}],
           "regn_open": 0, "status": "EXPIRED", "regnRequirements": {"end_regn_dt": "2026-11-20T00:00:00+05:30"},
           "jobDetail": {}}
    job = unstop.parse([row])[0]
    assert job.accepting is False and job.expires == "2026-11-20"
    assert unstop.parse([dict(row, regn_open=1, status="LIVE")])[0].accepting is True
    assert Rules(profile).prefilter(make_job(accepting=False)) == "applications-closed"

    from jobradar.pipeline import collect
    from jobradar.config import Settings

    store = Store(tmp_path / "jobs.json")
    tracked = store.add(make_job(source="unstop", board="unstop", native_id="5"), rule_score=70, signals=[],
                        min_years=None)
    settings = Settings(root=tmp_path, profile=profile, sources={"unstop": {"queries": ["sde"]}})

    async def fake_fetch(http, target):
        return [make_job(source="unstop", board="unstop", native_id="5", accepting=False)]

    import jobradar.sources as src

    real_fetch = src.fetch
    src.fetch = fake_fetch
    try:
        asyncio.run(collect(settings, store, log=lambda m: None))
    finally:
        src.fetch = real_fetch
    assert tracked["closed"] and "applications are closed" in tracked["closed_reason"]


def test_status_label_ages_new_jobs():
    now = today()
    rec = {"status": "new", "first_seen": now.isoformat(), "posted_at": (now - timedelta(days=2)).isoformat()}
    assert status_label(rec) == "new"
    assert status_label(dict(rec, posted_at=(now - timedelta(days=11)).isoformat())) == "to review"
    assert status_label(dict(rec, first_seen=(now - timedelta(days=8)).isoformat())) == "to review"
    assert status_label(dict(rec, posted_at=None)) == "new"
    assert status_label(dict(rec, status="applied")) == "applied"
