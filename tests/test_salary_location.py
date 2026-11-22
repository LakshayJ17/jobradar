import pytest

from jobradar.filters import Rules
from jobradar.salary import from_schema, in_description, label, parse_text, to_lpa
from jobradar.sources import lever, unstop
from jobradar.sources.jsonld import salary_from_label
from jobradar.store import Store


@pytest.mark.parametrize("text,want", [
    ("₹ 2,00,000 - 2,40,000 /year", [2.0, 2.4]),            # Internshala
    ("₹8L – ₹15L • 0.01% – 1.0%", [8.0, 15.0]),              # Wellfound (pay, then equity)
    ("$40k – $100k", [35.2, 88.0]),                          # USD, converted at the rough rate
    ("6-8 LPA", [6.0, 8.0]),
    ("CTC: 6 to 8 lakhs per annum", [6.0, 8.0]),
    ("Salary: 25,000 - 35,000 per month", [3.0, 4.2]),
    ("INR 12,00,000", [12.0, 12.0]),
    ("Rs. 4.5 Lacs", [4.5, 4.5]),
    ("25k per month", [3.0, 3.0]),
    ("Salary: 4.5L8L a year", [4.5, 8.0]),                  # Shine writes ranges without a separator
    ("Salary: 8K12K a month", [0.96, 1.44]),
    ("Salary: 96,000 - 1,44,000 per year", [0.96, 1.44]),
    ("Salary: 20K a month", [2.4, 2.4]),
    ("₹500 crore revenue", None),
    ("raised $20M", None),
    ("Competitive salary", None),
    ("[Salary Hidden]", None),
    ("2 years experience", None),
])
def test_parse_text(text, want):
    assert parse_text(text) == want


def test_description_pay_needs_a_pay_word_and_takes_the_best_mention():
    text = "Stipend during training: ₹15,000/month. After training, CTC of 6-9 LPA."
    assert in_description(text) == [6.0, 9.0]
    assert in_description("We have 500 employees, $2B revenue and 4.5 stars. 0-1 years experience.") is None
    assert in_description("Python, SQL. Bengaluru 560001.") is None


def test_structured_pay_fields():
    assert from_schema({"currency": "INR", "value": {"value": 0, "unitText": "YEAR"}}) is None  # 0 = hidden
    assert from_schema({"currency": "INR", "value": {"minValue": 20000, "maxValue": 30000, "unitText": "MONTH"}}) == [2.4, 3.6]
    assert to_lpa(20, 40, "USD") is None  # hourly pay in a yearly field
    row = {"id": 1, "title": "SDE", "organisation": {"name": "Acme"}, "locations": [{"city": "Noida", "country": "India"}],
           "jobDetail": {"min_salary": "600000", "max_salary": "900000", "currency": "fa-rupee", "pay_in": "annually",
                         "show_salary": 1}}
    assert unstop.parse([row])[0].salary == [6.0, 9.0]
    row["jobDetail"]["show_salary"] = 0
    assert unstop.parse([row])[0].salary is None  # the employer chose to hide it
    assert lever._salary({"min": 0, "max": 0, "currency": "INR", "interval": "per-year-salary"}) is None
    assert lever._salary({"min": 50000, "max": 70000, "currency": "INR", "interval": "per-month-salary"}) == [6.0, 8.4]
    assert label([6.0, 9.0]) == "₹6-9 LPA" and label([12.0, 12.0]) == "₹12 LPA" and label(None) == ""


def test_salary_from_page_label():
    ctc = r"CTC \(ANNUAL\)"
    assert salary_from_label('<span>CTC (ANNUAL)</span><div><span>₹ 2,00,000 - 2,40,000</span><span>/year</span>', ctc) == [2.0, 2.4]
    assert salary_from_label('<span>CTC (ANNUAL)</span><div>Competitive salary</div><p>Probation ₹ 10,000</p>', ctc) is None
    shine = r'salary_text\\?"\s*:\s*\\?"'
    assert salary_from_label(r'{\"salary_text\":\"[Salary Hidden]\",\"service_level_agreement\":0}', shine) is None
    assert salary_from_label(r'{\"salary_text\":\"Rs 3.5 - 5 Lakh/Yr\",\"a\":1}', shine) == [3.5, 5.0]


def test_salary_floor_hides_listed_low_pay_only(profile, make_job):
    rules = Rules(dict(profile, salary={"min_lpa": 6, "hide_unknown": False}))
    assert rules.prefilter(make_job(salary=[2.0, 2.4])) == "salary-below-6lpa"
    assert rules.prefilter(make_job(salary=[4.0, 7.0])) is None  # the top of the range clears the floor
    assert rules.prefilter(make_job()) is None and rules.postfilter(make_job()) is None  # pay not listed: kept
    strict = Rules(dict(profile, salary={"min_lpa": 6, "hide_unknown": True}))
    assert strict.prefilter(make_job()) is None  # only decided once the full posting has been read
    assert strict.postfilter(make_job()) == "salary-not-listed"
    assert Rules(dict(profile, salary={"min_lpa": 0})).prefilter(make_job(salary=[1.0, 1.0])) is None


def test_delhi_ncr_group_and_rank_bonus(profile, make_job):
    locs = dict(profile["locations"], preferred=["delhi ncr"], preferred_boost=10, remote_is_preferred=True)
    rules = Rules(dict(profile, locations=locs))
    for where in ("Gurgaon, Haryana", "Gurugram", "Noida, Uttar Pradesh", "Greater Noida", "Faridabad",
                  "Ghaziabad", "New Delhi, India", "Bangalore / Gurugram"):
        assert rules.rank_bonus(make_job(location=where)) == 10, where
        assert rules.location_ok(make_job(location=where)), where
    assert rules.rank_bonus(make_job(location="Bengaluru, India")) == 0
    assert rules.rank_bonus(make_job(location="Remote (India)", remote=True)) == 10
    assert rules.rank_bonus(make_job(location="Remote (US only)", remote=True)) == 0  # not open to India
    no_remote = Rules(dict(profile, locations=dict(locs, remote_is_preferred=False)))
    assert no_remote.rank_bonus(make_job(location="Remote (India)", remote=True)) == 0


def test_rank_bonus_orders_without_changing_score(tmp_path, make_job):
    store = Store(tmp_path / "j.json")
    far = store.add(make_job(native_id="b", location="Bengaluru"), rule_score=90, signals=[], min_years=None)
    near = store.add(make_job(native_id="n", location="Noida"), rule_score=82, signals=[], min_years=None)
    best = store.add(make_job(native_id="x", location="Pune"), rule_score=95, signals=[], min_years=None)
    near["rank_bonus"] = 10
    assert [r["id"] for r in store.ranked()] == [best["id"], near["id"], far["id"]]
    assert store.score_of(near) == 82


def test_refilter_applies_salary_floor_both_ways(tmp_path, make_job, profile):
    from jobradar.pipeline import refilter

    store = Store(tmp_path / "j.json")
    low = store.add(make_job(native_id="l", description="CTC: 2.5 - 3 LPA. Python."), rule_score=70, signals=[],
                    min_years=None)
    ok = store.add(make_job(native_id="o", location="Noida"), rule_score=70, signals=[], min_years=None)
    six = dict(profile, salary={"min_lpa": 6})
    assert refilter(store, Rules(six)) == (1, 0)
    assert low["salary"] == [2.5, 3.0] and low["status"] == "skipped" and low["auto_skip"] == "salary-below-6lpa"
    assert ok["status"] == "new"
    assert refilter(store, Rules(dict(profile, salary={"min_lpa": 2}))) == (0, 1)
    assert low["status"] == "new" and "auto_skip" not in low
