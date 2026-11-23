"""Internships: the profile switch (jobs only / both / internships only), stipends, sources and outputs."""

import asyncio
from pathlib import Path

import pytest

from jobradar.ai import score_system
from jobradar.filters import Rules, internship_mode, is_internship, is_unpaid
from jobradar.models import Job
from jobradar.report import pay_columns, pay_text, render_html
from jobradar.salary import stipend_label, stipend_short, to_lpa
from jobradar.sources import build_targets, google, jsonld, unstop
from jobradar.store import Store
from jobradar.util import today


@pytest.mark.parametrize("value, want", [
    (None, False), (False, False), (True, True), ("only", "only"), ("Only ", "only"), ("yes", True),
    ("include", True), ("no", False), ("false", False), ("sometimes", False),
])
def test_internship_mode(value, want):
    assert internship_mode({"internships": value}) == want


def test_older_include_internships_setting_still_works():
    assert internship_mode({"include_internships": True}) is True
    assert internship_mode({"internships": "only", "include_internships": False}) == "only"


def test_is_internship_and_unpaid():
    assert is_internship("Software Engineering Intern")
    assert is_internship("Web Development - Internship")
    assert is_internship("Web Development", "INTERN, FULL_TIME")
    assert not is_internship("Internal Tools Engineer")
    assert not is_internship("International Payments Engineer", "FULL_TIME")
    assert is_unpaid("VOLUNTEER, INTERN, PART_TIME")
    assert is_unpaid("Internship, unpaid")
    assert is_unpaid("", "Stipend: Unpaid\nDeadline: 2026-12-23")
    assert not is_unpaid("INTERN, FULL_TIME", "Stipend: 11,000 - 20,000 /month")


def test_jobs_only_both_or_internships_only(profile, make_job):
    intern, job = make_job(title="Software Engineering Intern"), make_job(title="Software Engineer")
    assert Rules(profile).prefilter(intern) == "internship"  # the template: jobs only
    assert Rules(profile).prefilter(job) is None
    profile["internships"] = True
    assert Rules(profile).prefilter(intern) is None and Rules(profile).prefilter(job) is None
    profile["internships"] = "only"
    assert Rules(profile).prefilter(intern) is None
    assert Rules(profile).prefilter(job) == "not-internship"
    assert Rules(profile).postfilter(job) == "not-internship"


@pytest.mark.parametrize("title, keep", [
    ("Web Development - Internship", True),        # Internshala names the work: a web developer's internship
    ("Python Django Development - Internship", True),
    ("Data Science - Internship", True),
    ("Machine Learning Intern", True),
    ("Software Engineering Intern, Summer 2027", True),
    ("Graphic Design - Internship", False),
    ("Human Resources (HR) - Internship", False),
    ("Digital Marketing - Internship", False),      # exclude_titles: marketing
    ("Business Development (Sales) - Internship", False),
    ("Data Entry - Internship", False),
    ("Web Development - Internship (WFH & Part Time)", False),  # exclude_titles: part-time
])
def test_internship_titles(profile, make_job, title, keep):
    profile["internships"] = True
    assert (Rules(profile).prefilter(make_job(title=title)) is None) is keep


def test_stipend_floor_is_separate_from_the_salary_floor(profile, make_job):
    profile["internships"] = True
    profile["salary"] = dict(profile["salary"], min=6, stipend_min=10000)
    rules = Rules(profile)
    paid = make_job(title="Data Science Intern", salary=to_lpa(11000, 20000, "INR", "month"))
    low = make_job(title="Data Science Intern", salary=to_lpa(3000, 5000, "INR", "month"))
    unpaid = make_job(title="Web Development - Internship", employment_type="VOLUNTEER, INTERN, PART_TIME")
    unknown = make_job(title="Data Science Intern")
    assert rules.prefilter(paid) is None  # ₹20k a month is under ₹6 LPA, but the job floor doesn't apply
    assert rules.prefilter(low) == "stipend-below-10k"
    assert rules.prefilter(unpaid) == "unpaid"
    assert rules.postfilter(unknown) is None  # unknown stipend is kept unless hide_unknown
    assert rules.prefilter(make_job(salary=[3.0, 4.0])) == "salary-below-6lpa"  # jobs still use salary.min
    profile["salary"]["stipend_min"] = 0
    assert Rules(profile).prefilter(unpaid) is None  # no floor: unpaid internships are shown


def test_internship_score_signals(profile, make_job):
    profile["internships"] = True
    job = make_job(title="Data Science - Internship", employment_type="INTERN, FULL_TIME",
                   salary=to_lpa(11000, 20000, "INR", "month"),
                   description="Duration: 6 Months. Other perks: Certificate, Job offer. Python, SQL.")
    score, signals, _ = Rules(profile).score(job)
    assert {"internship", "6 months", "job offer possible", "early-career role"} <= set(signals)
    assert "stipend ₹11k-20k a month" in signals
    plain = make_job(title="Data Science - Internship", description="Python, SQL.")
    assert score == Rules(profile).score(plain)[0] + 6


def test_stipends_shown_a_month():
    sal = to_lpa(11000, 20000, "INR", "month")
    assert sal == [1.32, 2.4]
    assert stipend_short(sal, "INR") == "11k-20k/mo" and stipend_label(sal, "INR") == "₹11k-20k a month"
    assert stipend_short(to_lpa(7500, 7500, "INR", "month"), "INR") == "7.5k/mo"
    assert to_lpa(1000, 1000, "INR", "month") == [0.12, 0.12]  # small stipends are real pay, not noise
    assert to_lpa(1000, 1000, "INR", "year") is None


def test_unstop_internships():
    row = {"id": 9, "title": "Software Internship", "type": "jobs", "subtype": "internships",
           "organisation": {"name": "Acme"}, "public_url": "internships/software-internship-acme-9",
           "locations": [], "regn_open": 1, "status": "LIVE", "details": "<p>Build web apps.</p>",
           "filters": [{"type": "eligible", "name": "Engineering Students"}, {"type": "eligible", "name": "Fresher"}],
           "jobDetail": {"type": "wfh", "timing": "full_time", "paid_unpaid": "unpaid", "pay_in": "monthly",
                         "show_salary": 1, "min_salary": None, "max_salary": None, "currency": "fa-rupee"}}
    job = unstop.parse([row])[0]
    assert job.employment_type == "Internship, unpaid, full_time"
    assert is_internship(job.title, job.employment_type) and is_unpaid(job.employment_type, job.description)
    assert job.description.startswith("Stipend: unpaid.")
    paid = unstop.parse([dict(row, jobDetail=dict(row["jobDetail"], paid_unpaid="paid", min_salary=15000,
                                                  max_salary=20000))])[0]
    assert paid.salary == [1.8, 2.4] and paid.employment_type == "Internship, full_time"
    assert unstop.parse([dict(row, subtype="jobs")])[0].employment_type == "full_time"  # jobs unchanged
    assert unstop.opportunities(False) == ("jobs",)
    assert unstop.opportunities(True) == ("jobs", "internships")
    assert unstop.opportunities("only") == ("internships",)


def test_sources_follow_the_setting():
    cfg = {"google": {"levels": ["EARLY"]}, "unstop": {},
           "scrape": [{"name": "Board", "kind": "jobs", "list_urls": []},
                      {"name": "Board", "board": "board-internships", "kind": "internships", "list_urls": []},
                      {"name": "Careers", "list_urls": []}]}
    boards = lambda mode: sorted(t.board for t in build_targets(cfg, internships=mode) if t.source == "scrape")
    assert boards(False) == ["board", "careers"]
    assert boards(True) == ["board", "board-internships", "careers"]
    assert boards("only") == ["board-internships", "careers"]
    assert all(t.opts["internships"] == "only" for t in build_targets(cfg, internships="only"))
    assert google.levels({"levels": ["EARLY"]}) == ["EARLY"]
    assert google.levels({"levels": ["EARLY"], "internships": True}) == ["EARLY", "INTERN_AND_APPRENTICE"]
    assert google.levels({"levels": ["EARLY"], "internships": "only"}) == ["INTERN_AND_APPRENTICE"]


def test_sources_yaml_internship_entry_is_read_only_when_wanted():
    import yaml

    cfg = yaml.safe_load((Path(__file__).parents[1] / "config" / "sources.yaml").read_text())
    names = lambda mode: {t.board for t in build_targets(cfg, internships=mode) if t.source == "scrape"}
    assert "internshala-internships" not in names(False)
    assert {"internshala", "internshala-internships"} <= names(True)
    assert "internshala" not in names("only") and "shine" not in names("only")


def test_internship_listing_marks_its_postings():
    class FakeHttp:
        async def get_html(self, url):
            return '<a href="/internship/detail/web-development-internship-in-pune-at-acme123">Web Development</a>'

    target = build_targets({"scrape": [{"name": "Internshala", "board": "internshala-internships",
                                        "kind": "internships", "list_urls": ["https://internshala.com/internships/x/"],
                                        "link_pattern": "^/internship/detail/",
                                        "title_from_url": "/internship/detail/(?:work-from-home-)?(.+?-internship)"}]},
                           internships=True)[0]
    job = asyncio.run(jsonld.fetch(FakeHttp(), target))[0]
    assert job.title == "web development internship" and job.employment_type == "Internship"
    jsonld.apply_posting(job, {"title": "Web Development", "employmentType": "FULL_TIME"})
    assert is_internship(job.title, job.employment_type)  # the page's own type doesn't hide where it came from
    jsonld.apply_posting(job, {"title": "Web Development - Internship", "employmentType": "INTERN, FULL_TIME"})
    assert job.employment_type == "INTERN, FULL_TIME"


def test_ai_prompt_follows_the_setting(profile):
    profile["graduation_year"] = today().year - 1  # already graduated
    assert "it is an internship;" in score_system(profile)
    profile["internships"] = True
    both = score_system(profile)
    assert "it is an internship;" not in both and "open to internships as well as full-time jobs" in both
    assert "open only to students still in college" in both
    profile["internships"] = "only"
    only = score_system(profile)
    assert "wants internships only" in only and "full-time job, not an internship" in only
    profile["graduation_year"] = today().year + 1  # still a student: no such rule
    assert "students still in college" not in score_system(profile)


def test_outputs_show_internships(tmp_path, make_job):
    store = Store(tmp_path / "j.json")
    intern = store.add(make_job(native_id="i", title="Data Science - Internship", employment_type="INTERN",
                                salary=to_lpa(11000, 20000, "INR", "month")), rule_score=70, signals=[], min_years=None)
    job = store.add(make_job(native_id="j", salary=[6.0, 9.0]), rule_score=70, signals=[], min_years=None)
    unpaid = store.add(make_job(native_id="u", title="Web Development - Internship",
                                employment_type="VOLUNTEER, INTERN"), rule_score=60, signals=[], min_years=None)
    assert pay_text(intern) == "₹11k-20k a month" and pay_text(intern, short=True) == "11k-20k/mo"
    assert pay_text(job) == "₹6-9 LPA" and pay_text(job, short=True) == "6-9"
    assert pay_text(unpaid) == "unpaid"
    assert pay_columns(intern) == (11000, 20000, "a month") and pay_columns(job) == (6.0, 9.0, "LPA")
    page = render_html(store, {})
    assert 'id="kind"' in page and '"intern": true' in page and '"intern": false' in page

    from openpyxl import load_workbook

    from jobradar.export import COLUMNS, write_xlsx

    ws = load_workbook(write_xlsx(store, tmp_path / "jobs.xlsx"))["All tracked"]
    col = [h for h, _ in COLUMNS].index("Type") + 1
    assert {ws.cell(r, col).value for r in range(2, ws.max_row + 1)} == {"Internship", "Job"}


def test_list_flags(tmp_path, make_job, monkeypatch, capsys):
    from jobradar import cli
    from jobradar.config import load_settings

    monkeypatch.setenv("JOBRADAR_HOME", str(tmp_path))
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "sources.yaml").write_text("{}\n")
    settings = load_settings()
    store = Store(settings.store_path)
    store.add(make_job(native_id="i", title="Data Science Intern"), rule_score=70, signals=[], min_years=None)
    store.add(make_job(native_id="j", title="Data Engineer"), rule_score=70, signals=[], min_years=None)
    store.save()
    assert cli.main(["list", "--internships"]) == 0
    out = capsys.readouterr().out
    assert "Data Science Intern" in out and "Data Engineer" not in out
    assert cli.main(["list", "--jobs"]) == 0
    out = capsys.readouterr().out
    assert "Data Engineer" in out and "Data Science Intern" not in out
