from datetime import timedelta

import pytest

from jobradar.filters import Rules, batch_years, min_years_required
from jobradar.util import today


@pytest.mark.parametrize("text, want", [
    ("WHAT YOU BRING\n- 8+ years strong expertise in using C++", 8),
    ("• 2–5 years in DevOps, SRE, infrastructure", 2),
    ("Additional Information\n- 8 - 12 years", 8),
    ("Additional Information\n4 to 8 years", 4),
    ("Minimum 3 years of hands-on experience", 3),
    ("two years of professional experience", 2),
    ("0-1 years of experience in Python", 0),
    ("Bachelor's + 3 years of experience, or Master's + 1 year of experience", 1),
    ("For over 30 years, Bosch has been a leader. Freshers welcome.", None),
    ("In the last 10+ years we grew 5x", None),
    ("a 4 year degree in computer science", None),
    ("Founded 25 years ago", None),
    ("", None),
])
def test_min_years_required(text, want):
    assert min_years_required(text) == want


@pytest.mark.parametrize("text, want", [
    ("Open to 2025/2026 batch graduates", {2025, 2026}),
    ("2027 Campus Hire_Associate Engineer_SW", {2027}),
    ("Eligibility: class of 2026", {2026}),
    ("candidates graduating in 2026 or 2027", {2026, 2027}),
    ("We were founded in 2015", set()),
])
def test_batch_years(text, want):
    assert batch_years(text) == want


@pytest.mark.parametrize("title, keep", [
    ("Software Engineer", True),
    ("Software Development Engineer I", True),
    ("SDE-1, Payments", True),
    ("Member of Technical Staff - Backend", True),
    ("AI Engineer", True),
    ("Graduate Engineer Trainee - Software", True),
    ("Technology Early Career Entry Program", True),
    ("Associate Engineer", True),
    ("Senior Software Engineer", False),
    ("Sr. Backend Developer", False),
    ("Software Engineer II", False),
    ("SDE 2", False),
    ("Software Engineer 3, Platform", False),
    ("Staff Engineer", False),
    ("Engineering Manager", False),
    ("Lead Member of Technical Staff", False),
    ("PMTS, Data Platform", False),
    ("Customer Journey - Software Engineer - AVP", False),
    ("Solutions Engineer", False),
    ("Business Developer", False),
    ("Graduate Engineering Technician", False),
    ("Account Executive", False),
    ("Hardware Design Engineer", False),
    ("Product Manager", False),
])
def test_title_rules(profile, make_job, title, keep):
    assert (Rules(profile).prefilter(make_job(title=title)) is None) is keep


@pytest.mark.parametrize("location, remote, keep", [
    ("Bengaluru, India", None, True),
    ("Pune", None, True),
    ("Gurugram, Haryana", None, True),
    ("San Francisco, CA", None, False),
    ("Remote", True, True),
    ("Remote - India", True, True),
    ("Remote (US)", True, False),
    ("Remote, APAC", None, True),
    ("London / Bangalore", None, True),
])
def test_location_rules(profile, make_job, location, remote, keep):
    assert Rules(profile).location_ok(make_job(location=location, remote=remote)) is keep


def test_internships_and_age(profile, make_job):
    rules = Rules(profile)
    assert rules.prefilter(make_job(title="Software Engineering Intern")) == "internship"
    assert rules.prefilter(make_job(employment_type="Intern")) == "internship"
    old = (today() - timedelta(days=60)).isoformat()
    assert rules.prefilter(make_job(posted_at=old)) == "too-old"
    assert rules.prefilter(make_job(posted_at=None)) is None  # unknown date is kept
    profile["include_internships"] = True
    assert Rules(profile).prefilter(make_job(title="Software Engineering Intern")) is None


def test_postfilter(profile, make_job):
    rules = Rules(profile)
    assert rules.postfilter(make_job(description="3+ years of experience with Java")) == "needs-3y-exp"
    assert rules.postfilter(make_job(description="0-2 years of experience")) is None
    assert rules.postfilter(make_job(description="Only for 2025 batch graduates")) == "batch-2025"
    assert rules.postfilter(make_job(description="2025/2026 batch passouts")) is None
    assert rules.postfilter(make_job(description="Assistant Vice President Expectations\n- lead a team")) == "excluded-text"
    assert rules.postfilter(make_job(level_hint="mid_senior_level", description="Build APIs")) == "senior-level"
    assert rules.postfilter(make_job(level_hint="mid_senior_level", description="Freshers welcome")) is None


def test_score_signals(profile, make_job):
    profile = dict(profile, skills=["python", "fastapi", "docker", "rag"])
    job = make_job(title="Graduate Software Engineer",
                   description="New grad role for the 2026 batch. 0-1 years of experience. Python, FastAPI, Docker, RAG.")
    score, signals, years = Rules(profile).score(job)
    assert years == 0
    assert score >= 85
    assert "early-career role" in signals and "2026 batch" in signals
    plain, *_ = Rules(profile).score(make_job(description="Maintain COBOL systems."))
    assert plain < score


def test_scam_and_part_time_filters(profile, make_job):
    rules = Rules(profile)
    assert rules.prefilter(make_job(title="Python Developer - Fresher (WFH & Part Time)")) == "title-excluded"
    for text in ("Candidates must pay a registration fee of Rs 2000", "Refundable deposit required before joining",
                 "Training charges: 15,000 INR, placement guaranteed"):
        assert rules.postfilter(make_job(description=text)) == "excluded-text"
    assert rules.postfilter(make_job(description="No fees are charged at any stage. Freshers welcome.")) is None
