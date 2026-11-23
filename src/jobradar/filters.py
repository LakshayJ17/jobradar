"""Rule-based screening (no AI), driven entirely by profile.yaml: roles, experience, levels, locations, pay.
Recall-oriented: the optional AI pass adds precision. The template is set up for fresher software roles in India,
but nothing here assumes that."""

from __future__ import annotations

import re

from .models import Job
from .salary import amount_k, currency_of, floor_lpa, stipend_floor, stipend_label
from .salary import label as salary_label
from .util import country_name, country_names, days_ago

_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
# Each optional part owns its trailing whitespace ("\s*(?:\+\s*)?", never "\s*\+?\s*"), so a long run of spaces
# can't be split between two \s* in every possible way (that was quadratic on hostile descriptions).
_YEARS_RX = re.compile(
    rf"\b{_NUM}\s*(?:(?:\+|plus)\s*)?(?:(?:-|\u2013|\u2014|to)\s*{_NUM}\s*(?:\+\s*)?)?(?:years?|yrs?)(?:'s)?\b", re.I)
_EXPERIENCE_RX = re.compile(r"experience|expertise|\bexp\b|work(?:ing)? (?:in|on|with)|industry|professional"
                            r"|hands[- ]on|proven|track record", re.I)
_NOT_EXPERIENCE_RX = re.compile(r"^\W*(?:old|of age|ago|in business|degree|program|course|bachelor|b\.?tech|warranty"
                                r"|of (?:history|innovation|excellence|growth|operations|service|legacy))", re.I)
# "for over 30 years, we have..." / "in the last 5+ years" describe the company, not the candidate
_HISTORY_BEFORE_RX = re.compile(r"(?:\bfor|\bover|\bsince|\bpast|\blast|\bnext|\bwithin|founded|history|celebrat\w*)"
                                r"\s*(?:the\s+)?(?:over|more than|nearly|almost|about)?\s*$", re.I)

# City groups: write "delhi ncr" in profile.yaml and every name the boards use for that area matches.
CITY_GROUPS = {
    "delhi ncr": ["delhi", "new delhi", "ncr", "delhi ncr", "gurugram", "gurgaon", "noida", "greater noida",
                  "gautam buddha nagar", "faridabad", "ghaziabad"],
    "bengaluru": ["bengaluru", "bangalore"],
    "mumbai": ["mumbai", "navi mumbai", "thane", "mumbai city"],
    "pune": ["pune", "pimpri", "hinjewadi"],
    "hyderabad": ["hyderabad", "secunderabad", "hitech city"],
    "chennai": ["chennai", "madras"],
    "kolkata": ["kolkata", "calcutta"],
}
CITY_GROUPS["ncr"] = CITY_GROUPS["delhi"] = CITY_GROUPS["delhi ncr"]
CITY_GROUPS["bangalore"] = CITY_GROUPS["bengaluru"]


def expand_cities(words: list, groups: dict | None = None) -> list[str]:
    """City and group names -> every name boards use. `groups` (profile locations.groups) adds your own."""
    table = {**CITY_GROUPS, **{str(k).strip().lower(): [str(c).strip().lower() for c in v or []]
                               for k, v in (groups or {}).items()}}
    out = []
    for w in words or []:
        w = str(w).strip().lower()
        out += table.get(w, [w]) if w else []
    return list(dict.fromkeys(out))


def _city_rx(words: list, groups: dict | None = None) -> re.Pattern | None:
    words = expand_cities(words, groups)
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.I) if words else None


_FRESHER_RX = re.compile(
    r"new grad|fresh(?:er|ers| graduate)|entry[- ]level|early[- ]career|recent(?:ly)? graduat|university grad"
    r"|campus|no (?:prior )?experience (?:is )?required|0\s*(?:-|\u2013|to)\s*[12]\s*(?:years?|yrs?)"
    r"|graduate (?:program|engineer|trainee)|trainee", re.I)
# "*+" (possessive): a long "2020/2021/..." list is read once, never re-split while searching for "batch".
_BATCH_RX = re.compile(
    r"((?:20\d\d\s*(?:/|,|&|and|or|-|\u2013)\s*)*+20\d\d)\s*(?:batch|pass[- ]?outs?|graduat\w*|campus|grads?\b|new grad)"
    r"|(?:batch|class)\s*(?:of\s*)?((?:20\d\d\s*(?:/|,|&|and|or|-|\u2013)\s*)*+20\d\d)"
    r"|graduat\w*\s+(?:in|by)\s+((?:20\d\d\s*(?:/|,|&|and|or)\s*)*+20\d\d)", re.I)
TEXT_LIMIT = 20_000  # characters of a description the rules read; real postings are far shorter


def _readable(text: str | None) -> str:
    """Description text for the rules: whitespace runs collapsed (line breaks kept) and length capped."""
    text = re.sub(r"[^\S\n]+", " ", (text or "")[: TEXT_LIMIT * 2])
    return re.sub(r"\s*\n\s*", "\n", text)[:TEXT_LIMIT]
# A title that only says "graduate / trainee / associate" must also name the field (profile early_career_title_needs).
TECH_FIELD = r"engineer|developer|software|technolog|programm|data|\bai\b|\bml\b|\bit\b|\bsde\b|cloud|cyber"
_INTERN_RX = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b", re.I)
# Internship titles often name the work, not the job: "Web Development - Internship" is a web developer's internship.
_DEVELOPMENT_RX = re.compile(r"\bdevelopment\b", re.I)
_UNPAID_RX = re.compile(r"\bunpaid\b|\bvolunteer\b", re.I)
_UNPAID_TEXT_RX = re.compile(r"stipend\s*:?\s*(?:unpaid|none|nil|not provided)|\bunpaid internship\b", re.I)
_JOB_OFFER_RX = re.compile(r"\bppo\b|pre[- ]?placement offer|(?:full[- ]time|job) offer|offer of (?:full[- ]time )?employment",
                           re.I)
_DURATION_RX = re.compile(r"duration\b\D{0,30}?(\d{1,2})\s*months?\b|\b(\d{1,2})[- ]months? (?:internship|duration)", re.I)
# Board level labels, grouped under the names profile.yaml uses in exclude_levels.
LEVELS = {"entry": {"entry_level", "internship", "university", "fresher"}, "senior": {"mid_senior_level"},
          "director": {"director"}, "executive": {"executive"}}
_ENTRY_LEVELS = LEVELS["entry"]


def internship_mode(profile: dict) -> bool | str:
    """profile.yaml internships: false (jobs only), true (jobs and internships) or "only" (internships only).
    The older include_internships: true still works."""
    value = profile.get("internships")
    if value is None:
        value = profile.get("include_internships", False)
    if isinstance(value, str):
        word = value.strip().lower()
        return "only" if word == "only" else word in ("true", "yes", "on", "include", "1")
    return bool(value)


def is_internship(title: str | None, employment_type: str | None = "") -> bool:
    """An internship (or co-op), from its title or the board's employment type."""
    return bool(_INTERN_RX.search(title or "")) or "intern" in (employment_type or "").lower()


def is_unpaid(employment_type: str | None, description: str | None = "") -> bool:
    """The posting says there is no stipend (Internshala marks these VOLUNTEER, Unstop "unpaid")."""
    return bool(_UNPAID_RX.search(employment_type or "") or _UNPAID_TEXT_RX.search(_readable(description)))


def remote_open_to(locs: dict) -> list[str]:
    """Words in a remote job's location that make it open to you. Default: your country (and APAC / Asia for
    India) plus anywhere / worldwide / global."""
    if locs.get("remote_open_to"):
        return [str(w).lower() for w in locs["remote_open_to"]]
    country = country_name(locs.get("country") or "")
    region = ["apac", "asia"] if country == "India" else []
    return country_names(country) + region + ["anywhere", "worldwide", "global"]


def _num(token: str) -> int:
    return int(token) if token.isdigit() else _NUM_WORDS[token.lower()]


def min_years_required(text: str) -> int | None:
    """Smallest 'N years of experience' requirement in the text (permissive: 'BS+3y or MS+1y' -> 1)."""
    text = _readable(text)
    found: list[int] = []
    for m in _YEARS_RX.finditer(text or ""):
        after = text[m.end(): m.end() + 70]
        before = text[max(0, m.start() - 50): m.start()]
        if _NOT_EXPERIENCE_RX.search(after):
            continue
        context = bool(_EXPERIENCE_RX.search(after) or re.search(r"experience|minimum|at least|require", before, re.I))
        # "3+ years" or "2-5 years" reads as a requirement even without the word "experience"
        strong = bool(m.group(2)) or bool(re.search(r"\+|plus", m.group(0)))
        if context or (strong and not _HISTORY_BEFORE_RX.search(before)):
            low = _num(m.group(1))
            if low <= 30:
                found.append(low)
    return min(found) if found else None


def batch_years(text: str) -> set[int]:
    years: set[int] = set()
    for m in _BATCH_RX.finditer(_readable(text)):
        chunk = next(g for g in m.groups() if g)
        years |= {int(y) for y in re.findall(r"20\d\d", chunk)}
    return years


class Rules:
    def __init__(self, profile: dict) -> None:
        self.p = profile
        roles = profile.get("roles") or {}
        self.early = [re.compile(x, re.I) for x in roles.get("early_career") or []]
        self.include = [re.compile(x, re.I) for fam, pats in roles.items() if fam != "early_career" for x in pats or []]
        self.exclude = [re.compile(x, re.I) for x in profile.get("exclude_titles") or []]
        self.exclude_desc = [re.compile(x, re.I) for x in profile.get("exclude_description") or []]
        locs = profile.get("locations") or {}
        groups = locs.get("groups") or {}
        self.loc_rx = _city_rx(locs.get("accept"), groups)
        self.pref_rx = _city_rx(locs.get("preferred"), groups)
        self.remote_open_rx = _city_rx(remote_open_to(locs))
        self.pref_boost = int(locs.get("preferred_boost", 10))
        self.remote_preferred = bool(locs.get("remote_is_preferred", True))
        pay = profile.get("salary") or {}
        self.currency = currency_of(profile)
        self.min_lpa = floor_lpa(pay, self.currency)
        self.min_stipend = stipend_floor(pay, self.currency)
        self.stipend_tag = amount_k(float(pay.get("stipend_min") or 0)) if self.min_stipend else ""
        self.floor_tag = (f"{float(pay.get('min') or 0):g}{'lpa' if self.currency == 'INR' else 'k-' + self.currency.lower()}"
                          if pay.get("min") not in (None, "") else f"{self.min_lpa:g}lpa")
        self.hide_unknown_pay = bool(pay.get("hide_unknown"))
        self.remote_ok = bool(locs.get("remote", True))
        self.max_years = int(profile.get("max_years_required", 1))
        self.min_years = int(profile.get("min_years_required") or 0)
        self.experience = int(profile.get("experience_years") or 0)
        self.early_candidate = self.experience <= 1  # fresher / junior: early-career wording raises the score
        self.excluded_levels = {lvl for name in profile.get("exclude_levels") or [] for lvl in LEVELS.get(str(name), ())}
        self.field_rx = re.compile(profile.get("early_career_title_needs") or TECH_FIELD, re.I)
        self.max_age = profile.get("max_age_days")
        self.internships = internship_mode(profile)  # False, True or "only"
        self.grad_year = int(profile.get("graduation_year") or 0)
        self.skills = [(s, re.compile(r"(?<![\w+#])" + re.escape(str(s)) + r"(?![\w+#])", re.I))
                       for s in profile.get("skills") or []]

    # ------------------------------------------------------------ stage 1: cheap checks on list data
    def location_ok(self, job: Job) -> bool:
        loc = job.location or ""
        if self.loc_rx and self.loc_rx.search(loc):
            return True
        if self.remote_ok and (job.remote or re.search(r"\bremote\b", loc, re.I)):
            stripped = re.sub(r"[^a-z]", "", loc.lower())
            return bool(self.remote_open_rx and self.remote_open_rx.search(loc)) or stripped in ("", "remote")
        return False

    def is_remote(self, job: Job) -> bool:
        return bool(job.remote or re.search(r"\bremote\b|work from home|\bwfh\b", job.location or "", re.I))

    def rank_bonus(self, job: Job) -> int:
        """Ranking lift for jobs in your preferred cities (and remote ones, which you can do from there)."""
        if self.pref_rx and self.pref_rx.search(job.location or ""):
            return self.pref_boost
        if self.pref_rx and self.remote_preferred and self.is_remote(job) and self.location_ok(job):
            return self.pref_boost
        return 0

    def salary_reason(self, job: Job, *, final: bool) -> str | None:
        """Drop jobs whose listed pay tops out below salary.min, and internships whose stipend tops out below
        salary.stipend_min (or that are unpaid). Unknown pay is kept unless hide_unknown (checked only once the full
        posting has been read: final=True)."""
        if is_internship(job.title, job.employment_type):
            if not self.min_stipend:
                return None
            if is_unpaid(job.employment_type, job.description):
                return "unpaid"
            if job.salary:
                return f"stipend-below-{self.stipend_tag}" if job.salary[1] < self.min_stipend else None
            return "stipend-not-listed" if final and self.hide_unknown_pay else None
        if not self.min_lpa:
            return None
        if job.salary:
            return f"salary-below-{self.floor_tag}" if job.salary[1] < self.min_lpa else None
        return "salary-not-listed" if final and self.hide_unknown_pay else None

    def kind_reason(self, job: Job) -> str | None:
        """Jobs or internships, as profile.yaml internships asks."""
        intern = is_internship(job.title, job.employment_type)
        if intern and not self.internships:
            return "internship"
        if not intern and self.internships == "only":
            return "not-internship"
        return None

    def prefilter(self, job: Job, *, ignore_age: bool = False) -> str | None:
        """Reason to drop the job, or None to keep it."""
        if job.accepting is False:
            return "applications-closed"
        title = job.title or ""
        # scraped listings often only know the location after the job page is read (checked again in postfilter)
        if not (job.needs_detail and not job.location) and not self.location_ok(job):
            return "location"
        if any(rx.search(title) for rx in self.exclude):
            return "title-excluded"
        if reason := self.kind_reason(job):
            return reason
        if not self._title_targeted(title, is_internship(title, job.employment_type)):
            return "title-not-target"
        if reason := self.salary_reason(job, final=False):
            return reason
        return None if ignore_age else self._too_old(job)

    def _title_targeted(self, title: str, internship: bool = False) -> bool:
        if any(rx.search(title) for rx in self.include) or (
                any(rx.search(title) for rx in self.early) and bool(self.field_rx.search(title))):
            return True
        if internship:  # an internship is early-career; "Web Development - Internship" names the work, not the job
            as_role = _DEVELOPMENT_RX.sub("developer", title)
            return any(rx.search(as_role) for rx in self.include) or bool(self.field_rx.search(as_role))
        return False

    def _too_old(self, job: Job) -> str | None:
        age = days_ago(job.posted_at)
        if self.max_age and age is not None and age > int(self.max_age):
            return "too-old"
        return None

    # ------------------------------------------------------------ stage 2: needs the description
    def postfilter(self, job: Job, *, ignore_age: bool = False) -> str | None:
        if job.accepting is False:  # the detail page can say so too (Workday canApply)
            return "applications-closed"
        text = _readable(job.description)
        if not ignore_age and self._too_old(job):  # detail pages can carry a more accurate (older) posting date
            return "too-old"
        if not self.location_ok(job):
            return "location"
        if any(rx.search(job.title or "") for rx in self.exclude):  # detail pages can refine the title
            return "title-excluded"
        if reason := self.kind_reason(job):  # detail pages can say it's an internship (employmentType INTERN)
            return reason
        if not self._title_targeted(job.title or "", is_internship(job.title, job.employment_type)):
            return "title-not-target"
        if any(rx.search(text) for rx in self.exclude_desc):
            return "excluded-text"  # grade markers / fee-charging scams (profile: exclude_description)
        years = min_years_required(text)
        if years is not None and years > self.max_years:
            return f"needs-{years}y-exp"
        if years is not None and years < self.min_years:
            return f"only-{years}y-exp"
        batches = batch_years(f"{job.title}\n{text}")
        if batches and self.grad_year and self.grad_year not in batches:
            return "batch-" + "/".join(str(y) for y in sorted(batches))
        if job.level_hint in self.excluded_levels:
            if job.level_hint in _ENTRY_LEVELS:
                return "entry-level"
            if not _FRESHER_RX.search(f"{job.title}\n{text}"):  # boards mislabel fresher roles as senior
                return "senior-level"
        return self.salary_reason(job, final=True)

    def score(self, job: Job) -> tuple[int, list[str], int | None]:
        """(0-100 rule score, human-readable signals, min years required)."""
        title, text = job.title or "", _readable(job.description)
        score, signals = 40, []
        years = min_years_required(text)
        intern = is_internship(title, job.employment_type)
        if intern:
            signals.append("internship")
            if m := _DURATION_RX.search(text):
                signals.append(f"{m.group(1) or m.group(2)} months")
            if _JOB_OFFER_RX.search(text):
                score += 6
                signals.append("job offer possible")
        if self.early_candidate:
            if any(rx.search(title) for rx in self.early) or job.level_hint in _ENTRY_LEVELS or intern:
                score += 20
                signals.append("early-career role")
            if _FRESHER_RX.search(text):
                score += 12
                signals.append("fresher wording")
            if years is not None and years <= self.max_years:
                score += 8
                signals.append(f"{years}y exp asked")
        elif years is not None and self.min_years <= years <= max(self.max_years, self.experience):
            score += 20 + (12 if years >= self.experience - 2 else 0)  # asks for about the experience you have
            signals.append(f"{years}y exp asked (fits you)")
        if self.grad_year and self.grad_year in batch_years(f"{title}\n{text}"):
            score += 10
            signals.append(f"{self.grad_year} batch")
        hits = [name for name, rx in self.skills if rx.search(text) or rx.search(title)]
        if hits:
            score += min(20, 2 * len(hits))
            signals.append("skills: " + ", ".join(hits[:6]))
        if self.pref_rx and self.pref_rx.search(job.location or ""):
            score += 4
            signals.append("preferred city")
        if job.salary:
            signals.append(f"stipend {stipend_label(job.salary, self.currency)}" if intern
                           else f"pay {salary_label(job.salary, self.currency)}")
        age = days_ago(job.posted_at)
        if age is not None and age <= 3:
            score += 4
            signals.append("posted in the last 3 days")
        return min(score, 100), signals, years
