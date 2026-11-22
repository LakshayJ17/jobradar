"""Scrapers and new API sources, fed with trimmed real page / payload shapes (no network)."""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest

from jobradar.filters import Rules
from jobradar.http import Blocked, Http, nofetch_reason
from jobradar.models import Job
from jobradar.sources import atlassian, build_targets, complete_listing, google, jsonld, oracle, remote, unstop, wellfound
from jobradar.store import Store
from jobradar.util import today

JOB_PAGE = """<html><head>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
 {"@type":"JobPosting","title":"Software Engineer - Fresher","datePosted":"2026-11-12T00:00:00+05:30",
  "validThrough":"2027-01-11T00:00:00+05:30","employmentType":["FULL_TIME"],
  "hiringOrganization":{"@type":"Organization","name":"Acme Labs"},
  "jobLocation":[{"@type":"Place","address":{"@type":"PostalAddress","addressLocality":"Pune","addressCountry":"IN"}}],
  "experienceRequirements":{"@type":"OccupationalExperienceRequirements","monthsOfExperience":36},
  "description":"&lt;p&gt;Build APIs in Python.&lt;/p&gt;","skills":"Python, SQL"}]}</script>
</head><body></body></html>"""

LISTING = """<html><script type="application/ld+json">{"@type":"ItemList","itemListElement":[
 {"@type":"ListItem","position":1,"name":"Backend Developer - Fresher","url":"https://jobs.example.com/job/detail/backend-1"}]}
</script>
<a href="/job/detail/backend-1?utm=x">Backend Developer</a>
<a href="/job/detail/ml-2"><span>ML Engineer</span></a>
<a href="https://evil.example.org/job/detail/3">Off-site</a>
<a href="/about">About</a></html>"""


def test_jsonld_posting_fields_and_experience():
    [posting] = jsonld.job_postings(JOB_PAGE)
    job = Job(source="scrape", board="x", company="X", native_id="1", title="hint", url="https://x/1")
    jsonld.apply_posting(job, posting)
    assert (job.title, job.company, job.location) == ("Software Engineer - Fresher", "Acme Labs", "Pune, India")
    assert job.posted_at == "2026-11-12" and job.expires == "2027-01-11" and job.employment_type == "FULL_TIME"
    assert "Build APIs in Python." in job.description and "Skills: Python, SQL" in job.description
    assert job.description.startswith("Experience required: 3 years")


def test_jsonld_posting_experience_is_enforced_by_rules(profile):
    [posting] = jsonld.job_postings(JOB_PAGE)
    job = Job(source="scrape", board="x", company="X", native_id="1", title="hint", url="https://x/1")
    jsonld.apply_posting(job, posting)
    assert Rules(profile).postfilter(job) == "needs-3y-exp"


def test_listing_links_itemlist_anchors_canonical_and_same_site_only():
    links = jsonld.listing_links(LISTING, "https://jobs.example.com/fresher-jobs/", r"^/job/detail/")
    assert set(links) == {"https://jobs.example.com/job/detail/backend-1", "https://jobs.example.com/job/detail/ml-2"}
    assert links["https://jobs.example.com/job/detail/ml-2"] == "ML Engineer"


def test_scraped_job_without_location_survives_prefilter_then_is_checked(profile):
    rules = Rules(profile)
    job = Job(source="scrape", board="x", company="X", native_id="1", title="Software Engineer",
              url="https://x/1", needs_detail=True)
    assert rules.prefilter(job) is None  # location unknown until the job page is read
    job.location, job.description = "Berlin, Germany", "Python"
    assert rules.postfilter(job) == "location"


def _google_page(rows, total):
    data = json.dumps([rows, None, total, 20])
    return f"<script>AF_initDataCallback({{key: 'ds:1', hash: '2', data:{data}, sideChannel: {{}}}});</script>"


def test_google_parse_page():
    row = ["123", "Software Engineer, University Graduate", "https://signin", [None, "<li>Build things</li>"],
           [None, "<h3>Minimum qualifications:</h3><li>BS in CS</li>"], "x", None, "Google", "en-US",
           [["Bengaluru, Karnataka, India", [], "Bengaluru"]], [None, "About the job"], [2], [1794494305, 0]]
    jobs, total, size = google.parse_page(_google_page([row], 1), "EARLY")
    assert (total, size) == (1, 20)
    [job] = jobs
    assert job.url.endswith("/123-software-engineer-university-graduate")
    assert job.location == "Bengaluru, Karnataka, India" and job.level_hint == "entry_level"
    assert "Build things" in job.description and "BS in CS" in job.description and job.posted_at.startswith("2026-")
    with pytest.raises(Exception):
        google.parse_page("<html>changed layout</html>")


def test_unstop_parse_eligibility_and_location():
    rows = [{"id": 1, "title": "Software Engineer", "public_url": "jobs/se-acme-1", "organisation": {"name": "Acme"},
             "filters": [{"type": "eligible", "name": "Fresher"}], "locations": [{"city": "Pune", "country": "India"}],
             "jobDetail": {"type": "in_office", "min_experience": 0, "timing": "full_time"}, "details": "<p>Join us</p>",
             "approved_date": "2026-11-12 16:17:30 GMT+0530", "end_date": "2026-11-26T00:00:00+05:30"},
            {"id": 2, "title": "Backend Developer", "public_url": "jobs/be-2", "organisation": {"name": "B"},
             "filters": [{"type": "eligible", "name": "Experienced Professionals"}], "locations": [],
             "jobDetail": {"type": "wfh", "locations": []}, "details": ""}]
    a, b = unstop.parse(rows)
    assert a.url == "https://unstop.com/jobs/se-acme-1" and a.location == "Pune, India" and a.level_hint == "fresher"
    assert a.posted_at == "2026-11-12" and a.expires == "2026-11-26" and "Experience required: 0 years" in a.description
    assert b.level_hint == "mid_senior_level" and b.remote is True and b.location.startswith("Remote")


def test_wellfound_parse_page():
    state = {"StartupResult:1": {"name": "Acme", "highConcept": "AI for X",
                                 "highlightedJobListings": [{"__ref": "JobListingSearchResult:9"}]},
             "JobListingSearchResult:9": {"id": "9", "slug": "ai-engineer", "title": "AI Engineer", "remote": True,
                                          "locationNames": ["Bengaluru"], "acceptedRemoteLocationNames": ["India"],
                                          "yearsExperienceMin": 0, "liveStartAt": 1794494305, "jobType": "full-time",
                                          "description": "Build agents"}}
    page = ('<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps({"props": {"pageProps": {"apolloState": {"data": state}}}}) + "</script>")
    [job] = wellfound.parse_page(page)
    assert job.url == "https://wellfound.com/jobs/9-ai-engineer" and job.company == "Acme"
    assert job.location == "Bengaluru / Remote (India)" and job.level_hint == "entry_level"
    assert "Experience required: 0 years" in job.description


def test_remote_boards_and_location_rule(profile):
    rules = Rules(profile)
    h = remote.parse_himalayas([{"title": "Backend Engineer", "companyName": "A", "guid": "https://h/1",
                                 "locationRestrictions": ["Ireland"], "seniority": ["Mid-level"], "pubDate": 1794494305},
                                {"title": "Backend Engineer", "companyName": "B", "guid": "https://h/2",
                                 "locationRestrictions": [], "seniority": ["Entry-level"], "pubDate": 1794494305}])
    assert [rules.location_ok(j) for j in h] == [False, True] and h[1].level_hint == "entry_level"
    r = remote.parse_remoteok([{"legal": "notice"}, {"id": "5", "position": "Python Developer", "company": "C",
                                                     "location": "Worldwide", "date": "2026-11-13T08:00:21+00:00"}])
    assert len(r) == 1 and rules.location_ok(r[0])
    j = remote.parse_jobicy([{"id": 7, "jobTitle": "AI Engineer", "companyName": "D", "jobGeo": "China",
                              "jobLevel": "Senior", "jobType": ["Full-Time"], "url": "https://j/7"}])
    assert not rules.location_ok(j[0]) and j[0].level_hint == "mid_senior_level"


def test_oracle_country_facet_and_requisitions():
    assert oracle.find_country([{"locationsFacet": [{"Id": 5, "Name": "KARNATAKA, India"}, {"Id": 7, "Name": "India"}]}]) == 7
    t = build_targets({"oracle": [{"name": "JPMorgan Chase", "host": "jpmc.fa.oraclecloud.com", "site": "CX_1001"}]})[0]
    [job] = oracle.parse_requisitions([{"requisitionList": [
        {"Id": "21", "Title": "Software Engineer I", "PostedDate": "2026-11-16", "PrimaryLocation": "Mumbai",
         "PrimaryLocationCountry": "IN", "secondaryLocations": [{"Name": "Bengaluru"}]}]}], t)
    assert job.location == "Mumbai / Bengaluru, India"
    assert job.url == "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/job/21"
    assert 'Id="21"' in job.detail["params"]["finder"]


def test_atlassian_parse():
    [job] = atlassian.parse([{"id": 3, "title": "Graduate Software Engineer", "locations": ["Bengaluru -  India"],
                              "portalJobPost": {"updatedDate": "2026-11-09 12:21 PM"}, "overview": "<p>Hi</p>"}])
    assert job.posted_at == "2026-11-09" and job.location == "Bengaluru - India" and job.description == "Hi"


def test_nofetch_list_blocks_before_any_request():
    calls = []

    async def go():
        async with Http(transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))) as http:
            for url in ("https://www.linkedin.com/jobs/search", "https://in.indeed.com/jobs", "https://www.naukri.com/x"):
                with pytest.raises(Blocked):
                    await http.get_html(url)

    asyncio.run(go())
    assert calls == [] and nofetch_reason("https://jobs.example.com") is None


def test_circuit_breaker_and_bot_challenge():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        hits["n"] += 1
        if request.url.host == "walled.example.com":
            return httpx.Response(200, text="<html><title>Just a moment...</title>cf-chl</html>")
        return httpx.Response(403)

    async def go():
        async with Http(transport=httpx.MockTransport(handler)) as http:
            for _ in range(2):
                with pytest.raises(Exception):
                    await http.get_json("https://api.example.com/x")
            with pytest.raises(Blocked):  # circuit open: no third request
                await http.get_json("https://api.example.com/x")
            with pytest.raises(Blocked):
                await http.get_html("https://walled.example.com/jobs")
            return hits["n"]

    assert asyncio.run(go()) == 3


def test_partial_listings_never_close_jobs_and_expiry_does(tmp_path, make_job):
    assert complete_listing("greenhouse") and not complete_listing("scrape") and not complete_listing("unstop")
    store = Store(tmp_path / "j.json")
    store.add(make_job(native_id="a", expires=(today() - timedelta(days=1)).isoformat()), rule_score=60, signals=[],
              min_years=None)
    store.add(make_job(native_id="b", expires=(today() + timedelta(days=5)).isoformat()), rule_score=60, signals=[],
              min_years=None)
    assert store.close_expired() == 1
    assert store.get(1)["closed"] and not store.get(2)["closed"]


def test_rippling_list_and_job_page():
    from jobradar.sources import rippling

    [job] = rippling.parse([{"uuid": "u1", "name": "Software Engineer", "url": "https://ats.rippling.com/x/jobs/u1",
                             "workLocation": {"label": "Bengaluru, India"}, "department": {"label": "Eng"}}], "x", "X")
    assert job.key == "rippling:x:u1" and job.needs_detail and job.location == "Bengaluru, India"
    page = ('<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"apiData": {
        "jobPost": {"description": {"company": "<p>About X</p>", "role": "<p>Build APIs</p>"},
                    "createdOn": "2026-11-12T07:10:25.704000-08:00"}}}}}) + "</script>")
    info = rippling.parse_job_page(page)
    assert info["description"].startswith("Build APIs") and info["posted_at"] == "2026-11-12"
