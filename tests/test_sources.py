"""Parsers for each board, fed with trimmed real payload shapes (no network)."""

import asyncio

import httpx

from jobradar.http import Http, Robots
from jobradar.sources import Target, amazon, ashby, build_targets, eightfold, greenhouse, lever, smartrecruiters, workday


def test_greenhouse_parse_unescapes_html():
    data = {"jobs": [{"id": 7, "title": " Software Engineer ", "absolute_url": "https://x/7",
                      "location": {"name": "Bengaluru"}, "offices": [{"name": "India"}],
                      "first_published": "2026-11-10T10:00:00-04:00",
                      "content": "&lt;p&gt;Build &amp;amp; ship&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;",
                      "departments": [{"name": "Eng"}]}]}
    [job] = greenhouse.parse(data, "acme", "Acme")
    assert job.title == "Software Engineer" and job.posted_at == "2026-11-10"
    assert job.location == "Bengaluru (India)"
    assert "Build & ship" in job.description and "- Python" in job.description
    assert job.key == "greenhouse:acme:7"


def test_lever_parse_country_and_lists():
    data = [{"id": "abc", "text": "Backend Engineer", "hostedUrl": "https://jobs.lever.co/x/abc", "country": "IN",
             "categories": {"location": "Bangalore", "allLocations": ["Bangalore"], "commitment": "Full-time"},
             "workplaceType": "hybrid", "createdAt": 1794831594000, "descriptionPlain": "About us",
             "lists": [{"text": "Requirements", "content": "<li>2+ years Go</li>"}], "additionalPlain": "Perks"}]
    [job] = lever.parse(data, "x", "X")
    assert job.location == "Bangalore, India" and job.employment_type == "Full-time"
    assert "Requirements" in job.description and "2+ years Go" in job.description
    assert job.posted_at.startswith("2026-")


def test_ashby_parse_secondary_india_and_unlisted():
    data = {"jobs": [
        {"id": "1", "title": "ML Engineer", "location": "Remote", "isRemote": True, "isListed": True,
         "secondaryLocations": [{"location": "Bengaluru", "address": {"postalAddress": {"addressCountry": "India"}}}],
         "publishedAt": "2026-11-01T00:00:00.000+00:00", "jobUrl": "https://jobs.ashbyhq.com/x/1",
         "descriptionPlain": "Train models", "employmentType": "FullTime"},
        {"id": "2", "title": "Hidden", "isListed": False}]}
    jobs = ashby.parse(data, "x", "X")
    assert len(jobs) == 1
    assert jobs[0].location == "Remote / Bengaluru, India" and jobs[0].remote is True


def test_smartrecruiters_parse_level_hint():
    [job] = smartrecruiters.parse([{"id": "99", "name": "Software Engineer", "releasedDate": "2026-11-12T08:00:00.000Z",
                                    "location": {"city": "Pune", "region": "MH", "country": "in", "remote": False},
                                    "experienceLevel": {"id": "entry_level"}, "typeOfEmployment": {"label": "Full-time"}}],
                                  "Acme", "Acme")
    assert job.location == "Pune, MH, India" and job.level_hint == "entry_level" and job.needs_detail


def test_workday_country_facet_and_posting():
    facets = [{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Engineering", "id": "e"}]},
              {"facetParameter": "locationMainGroup", "values": [
                  {"facetParameter": "locationHierarchy1", "values": [{"descriptor": "India", "id": "IN-ID"},
                                                                       {"descriptor": "Israel", "id": "IL"}]}]}]
    assert workday.find_country_facet(facets) == ("locationHierarchy1", "IN-ID")
    assert workday.find_country_facet([]) is None
    t = Target("workday", "acme", "Acme", {"tenant": "acme", "wd": "wd5", "site": "Ext"})
    job = workday.parse_posting({"title": "Engineer", "externalPath": "/job/Pune/Engineer_R1",
                                 "locationsText": "2 Locations", "postedOn": "Posted 3 Days Ago"}, t, "India")
    assert job.location == "India (2 Locations)"
    us = [{"facetParameter": "Location_Country", "values": [{"descriptor": "United States of America", "id": "US-ID"}]}]
    assert workday.find_country_facet(us, "United States") == ("Location_Country", "US-ID")
    assert workday.find_country_facet(us, "India") is None
    assert job.url == "https://acme.wd5.myworkdayjobs.com/en-US/Ext/job/Pune/Engineer_R1"
    assert job.detail["url"].endswith("/wday/cxs/acme/Ext/job/Pune/Engineer_R1")


def test_amazon_parse():
    job = amazon.parse_job({"id_icims": "123", "title": "SDE I", "job_path": "/en/jobs/123/sde", "country_code": "IND",
                            "normalized_location": "Bengaluru, Karnataka", "posted_date": "November 13, 2026",
                            "description": "Build", "basic_qualifications": "- 1+ years of experience<br/>- CS degree",
                            "university_job": 1, "company_name": "ADCI - Karnataka"})
    assert job.company == "Amazon" and job.level_hint == "university" and job.posted_at == "2026-11-13"
    assert job.location == "Bengaluru, Karnataka, India" and "1+ years" in job.description


def test_eightfold_parse():
    t = Target("eightfold", "microsoft", "Microsoft", {"host": "apply.careers.microsoft.com", "domain": "microsoft.com"})
    job = eightfold.parse_position({"id": 5, "name": "Software Engineer", "locations": ["India, Karnataka, Bangalore"],
                                    "postedTs": 1794831594, "positionUrl": "/careers/job/5"}, t)
    assert job.url == "https://apply.careers.microsoft.com/careers/job/5"
    assert job.detail["params"]["position_id"] == "5"


def test_build_targets_and_only_filter():
    cfg = {"greenhouse": {"stripe": "Stripe"}, "lever": {"cred": "CRED"},
           "workday": [{"name": "NVIDIA", "tenant": "nvidia", "wd": "wd5", "site": "X"}], "amazon": {"queries": ["sde"]},
           "eightfold": [{"name": "Microsoft", "host": "h", "domain": "microsoft.com"}], "eightfold_queries": ["q"]}
    targets = build_targets(cfg)
    assert [t.label for t in targets] == ["greenhouse:stripe", "lever:cred", "workday:nvidia", "amazon:amazon",
                                          "eightfold:microsoft"]
    assert targets[-1].opts["queries"] == ["q"]
    assert [t.label for t in build_targets(cfg, only=["nvidia", "lever"])] == ["lever:cred", "workday:nvidia"]


def test_robots_longest_match():
    r = Robots("User-agent: *\nDisallow: /\nAllow: /api/pcsx\n\nUser-agent: BadBot\nDisallow: /")
    assert r.allowed("/api/pcsx/search?q=1") is True
    assert r.allowed("/careers") is False
    r2 = Robots("User-agent: *\nDisallow: /internal\nDisallow: /*.pdf$")
    assert r2.allowed("/en/search.json") is True and r2.allowed("/internal/x") is False
    assert r2.allowed("/a/b.pdf") is False and r2.allowed("/a/b.pdf?x") is True
    assert Robots("").allowed("/anything") is True
    assert Robots("User-agent: jobradar\nDisallow: /\n\nUser-agent: *\nAllow: /").allowed("/x") is False


def test_http_retries_429_then_succeeds_and_respects_robots():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private")
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with Http(transport=httpx.MockTransport(handler)) as http:
            assert await http.get_json("https://example.com/data", robots=True) == {"ok": True}
            try:
                await http.get_json("https://example.com/private/x", robots=True)
            except Exception as exc:  # Blocked
                return type(exc).__name__
        return None

    assert asyncio.run(go()) == "Blocked"
    assert calls["n"] == 2
