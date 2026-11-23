"""Nothing is tied to the template's defaults: experience level, country, currency, roles and city groups all come
from profile.yaml. Each test builds a profile unlike the template (an experienced engineer, a job search in the US)."""

import asyncio

import pytest

from jobradar import salary
from jobradar.ai import candidate, looking_for, score_system
from jobradar.cli import profile_warnings
from jobradar.filters import Rules
from jobradar.http import FetchError
from jobradar.sources import Target, amazon, eightfold, localize, remote, smartrecruiters, wellfound
from jobradar.sources.oracle import find_country
from jobradar.util import country_code, country_name, with_country


@pytest.fixture
def senior(profile):
    """Four years in, looking for mid-level backend roles."""
    return dict(profile, experience_years=4, max_years_required=6, min_years_required=2,
                exclude_levels=["director", "executive"], exclude_titles=["manager|director|intern"],
                looking_for="backend engineering roles", graduation_year=2021, degree="B.E. Computer Engineering")


@pytest.fixture
def usa(profile):
    return dict(profile, locations={"country": "United States", "accept": ["united states", "usa", "new york", "austin"],
                                    "remote": True, "preferred": ["bay area"],
                                    "groups": {"bay area": ["san francisco", "palo alto", "mountain view"]}},
                salary={"currency": "USD", "min": 110})


def test_experienced_profile_keeps_mid_level_jobs_and_drops_fresher_ones(senior, make_job):
    rules = Rules(senior)
    mid = make_job(title="Senior Software Engineer", level_hint="mid_senior_level",
                   description="4+ years of experience building APIs in Python.")
    assert rules.prefilter(mid) is None and rules.postfilter(mid) is None
    assert rules.postfilter(make_job(description="0-1 years of experience. Freshers welcome.")) == "only-0y-exp"
    assert rules.postfilter(make_job(description="8+ years of experience")) == "needs-8y-exp"
    assert rules.postfilter(make_job(level_hint="director", description="Lead the org")) == "senior-level"
    score, signals, _ = rules.score(mid)
    assert "4y exp asked (fits you)" in signals and "early-career role" not in signals and score >= 72
    fresher, fsig, _ = rules.score(make_job(title="Graduate Engineer", description="New grad role, no experience required"))
    assert "early-career role" not in fsig and fresher < score


def test_template_still_drops_board_senior_labels(profile, make_job):
    rules = Rules(profile)
    assert rules.postfilter(make_job(level_hint="mid_senior_level", description="Build APIs")) == "senior-level"
    entry_only = Rules(dict(profile, exclude_levels=["entry"]))
    assert entry_only.postfilter(make_job(level_hint="entry_level", description="Build APIs")) == "entry-level"


def test_ai_prompt_follows_the_profile(profile, senior, usa):
    fresher = score_system(profile)
    assert "recent graduate with under 1 year" in fresher and "outside India" in fresher and "Master's or PhD" in fresher
    assert "software engineering, AI / ML or data roles" in fresher and '"freshers"' in fresher
    exp = score_system(dict(senior, locations=usa["locations"], degree="M.S. Computer Science"))
    assert "4 year(s) of full-time experience" in exp and "recent graduate" not in exp and "India" not in exp
    assert "MORE than 6 year(s)" in exp and "less than 2 year(s)" in exp and "backend engineering roles" in exp
    assert "outside United States" in exp and "requires a PhD" in exp and "Master's" not in exp
    assert candidate(senior) == "B.E. Computer Engineering, class of 2021, 4 year(s) of full-time experience"
    assert looking_for(dict(profile, looking_for="")) == "software, ai, data roles"


def test_us_profile_locations_and_pay(usa, make_job):
    rules = Rules(usa)
    assert rules.location_ok(make_job(location="Austin, TX, United States"))
    assert not rules.location_ok(make_job(location="Bengaluru, India"))
    assert rules.location_ok(make_job(location="Remote - United States", remote=True))
    assert not rules.location_ok(make_job(location="Remote (India)", remote=True))
    assert rules.rank_bonus(make_job(location="Palo Alto, CA")) == 10  # your own city group
    us_job = make_job(location="Austin, TX", salary=salary.parse_text("$90k - $105k"))
    assert salary.label(us_job.salary, "USD") == "$90k-105k"
    assert rules.prefilter(us_job) == "salary-below-110k-usd"
    assert rules.prefilter(make_job(location="New York, NY", salary=salary.parse_text("$100k - $140k"))) is None
    assert salary.in_description("Base salary: 120,000 - 150,000 per year.", "USD") == salary.parse_text("$120k-$150k")
    # formats seen on real US postings (NVIDIA, Amazon): currency after the amount, pay far from the word "salary"
    nvidia = "The expected base salary ranges for this position are: 86,250 USD \u2013 169,000 USD for Level 2."
    assert salary.label(salary.in_description(nvidia, "USD"), "USD") == "$86k-169k"
    amazon_text = "Learn more about benefits.\n\nUSA, CA, Cupertino - 165,200.00 - 223,400.00 USD annually"
    assert salary.label(salary.in_description(amazon_text, "USD"), "USD") == "$165k-223k"
    assert salary.in_description("We grew 30% per year and have 2,000 employees. 24 days of leave per year.") is None


def test_legacy_min_lpa_still_works(profile, make_job):
    rules = Rules(dict(profile, salary={"min_lpa": 6}))
    assert rules.prefilter(make_job(salary=[2.0, 2.4])) == "salary-below-6lpa"
    assert salary.floor_lpa({"min": 6}, "INR") == 6 and salary.floor_lpa({"min_lpa": 6, "min": 8}, "INR") == 8


def test_currency_display():
    assert salary.label([6.0, 9.0], "INR") == "₹6-9 LPA" and salary.label([17.6, 26.4], "INR") == "₹17.6-26.4 LPA"
    assert salary.label([88.0, 105.6], "USD") == "$100k-120k" and salary.short([88.0, 105.6], "USD") == "100-120"
    assert salary.label([63.0, 63.0], "CAD") == "CAD 100k" and salary.unit("USD") == "USD k" and salary.unit("INR") == "LPA"
    assert salary.parse_text("$100k", default_currency="CAD") == [63.0, 63.0]  # a bare $ is your own dollar


def test_country_helpers():
    assert with_country("Bengaluru", "IN") == "Bengaluru, India"
    assert with_country("Austin, TX", "USA") == "Austin, TX, United States"
    assert with_country("Remote, United States of America", "US") == "Remote, United States of America"
    assert with_country("Somewhere", "ZZ") == "Somewhere" and with_country("", "GB") == "United Kingdom"
    assert country_name("uk") == "United Kingdom" and country_code("India", alpha=3) == "IND"
    assert find_country([{"locationsFacet": [{"Name": "United States", "Id": 7}]}], "USA") == 7


class FakeHttp:
    """Records what each source asks for; answers with an empty result."""

    def __init__(self):
        self.calls = []

    async def robots_allowed(self, url):
        return True

    async def get_json(self, url, params=None, **kw):
        self.calls.append((url, params or {}))
        return {}

    async def get_html(self, url, **kw):
        self.calls.append((url, {}))
        return "<html></html>"


@pytest.mark.parametrize("country,amazon_code,eightfold_loc,wellfound_slug,jobicy_geo", [
    ("India", "IND", "India", "india", "apac"),
    ("United States", "USA", "United States", "united-states", "usa"),
    ("Kenya", "KEN", "Kenya", "kenya", None),
])
def test_sources_search_your_country(country, amazon_code, eightfold_loc, wellfound_slug, jobicy_geo):
    def run(module, target):
        http = FakeHttp()
        try:
            asyncio.run(module.fetch(http, localize([target], country)[0]))
        except FetchError:
            pass  # the fake page is empty; only the request matters here
        return http.calls

    assert run(amazon, Target("amazon", "amazon", "Amazon", {"queries": ["sde"]}))[0][1]["country"] == amazon_code
    ef = run(eightfold, Target("eightfold", "microsoft", "Microsoft",
                               {"host": "x.example", "domain": "microsoft.com", "queries": ["sde"]}))
    assert ef[0][1]["location"] == eightfold_loc
    assert run(wellfound, Target("wellfound", "wellfound", "Wellfound", {"roles": ["software-engineer"]}))[0][0] \
        .endswith(f"/role/l/software-engineer/{wellfound_slug}")
    sr = run(smartrecruiters, Target("smartrecruiters", "Acme", "Acme"))
    assert sr[0][1]["country"] == country_code(country).lower()
    jobicy = run(remote, Target("remote", "jobicy", "Jobicy"))
    assert jobicy[0][1].get("geo") == jobicy_geo
    pinned = localize([Target("amazon", "amazon", "Amazon", {"country": "Canada"})], country)[0]
    assert pinned.opts["country"] == "Canada"  # a country set in sources.yaml wins


def test_doctor_flags_settings_that_contradict(profile, senior, usa):
    assert profile_warnings(profile) == []
    us_with_indian_cities = dict(usa, locations=dict(usa["locations"], accept=["bengaluru"]))
    assert any("doesn't mention United States" in w for w in profile_warnings(us_with_indian_cities))
    assert any("exclude_levels" in w for w in profile_warnings(dict(senior, exclude_levels=["senior"])))
    assert any("currency XYZ" in w for w in profile_warnings(dict(profile, salary={"currency": "xyz"})))
    assert any("min_years_required" in w for w in profile_warnings(dict(profile, min_years_required=3)))
