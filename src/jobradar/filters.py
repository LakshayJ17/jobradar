"""Rule-based screening (no AI), driven entirely by profile.yaml: roles, experience, levels, locations, pay.
Recall-oriented: the optional AI pass adds precision. The template is set up for fresher software roles in India,
but nothing here assumes that."""

from __future__ import annotations

import re

from .models import Job
from .salary import currency_of, floor_lpa
from .salary import label as salary_label
from .util import country_name, country_names, days_ago

_NUM_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d{1,2}|zero|one|two|three|four|five|six|seven|eight|nine|ten)"
_YEARS_RX = re.compile(
    rf"\b{_NUM}\s*(?:\+|plus)?\s*(?:(?:-|–|—|to)\s*{_NUM}\s*\+?\s*)?(?:years?|yrs?)(?:'s)?\b", re.I)
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
    r"|campus|no (?:prior )?experience (?:is )?required|0\s*(?:-|–|to)\s*[12]\s*(?:years?|yrs?)"
    r"|graduate (?:program|engineer|trainee)|trainee", re.I)
_BATCH_RX = re.compile(
    r"((?:20\d\d\s*(?:/|,|&|and|or|-|–)\s*)*20\d\d)\s*(?:batch|pass[- ]?outs?|graduat\w*|campus|grads?\b|new grad)"
    r"|(?:batch|class)\s*(?:of\s*)?((?:20\d\d\s*(?:/|,|&|and|or|-|–)\s*)*20\d\d)"
    r"|graduat\w*\s+(?:in|by)\s+((?:20\d\d\s*(?:/|,|&|and|or)\s*)*20\d\d)", re.I)
# A title that only says "graduate / trainee / associate" must also name the field (profile early_career_title_needs).
TECH_FIELD = r"engineer|developer|software|technolog|programm|data|\bai\b|\bml\b|\bit\b|\bsde\b|cloud|cyber"
_INTERN_RX = re.compile(r"\bintern(?:ship)?s?\b|\bco-?op\b", re.I)
# Board level labels, grouped under the names profile.yaml uses in exclude_levels.
LEVELS = {"entry": {"entry_level", "internship", "university", "fresher"}, "senior": {"mid_senior_level"},
          "director": {"director"}, "executive": {"executive"}}
_ENTRY_LEVELS = LEVELS["entry"]


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
    for m in _BATCH_RX.finditer(text or ""):
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
        self.internships = bool(profile.get("include_internships"))
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
        """Drop jobs whose listed pay tops out below salary.min_lpa. Unknown pay is kept unless hide_unknown
        (checked only once the full posting has been read: final=True)."""
        if not self.min_lpa:
            return None
        if job.salary:
            return f"salary-below-{self.floor_tag}" if job.salary[1] < self.min_lpa else None
        return "salary-not-listed" if final and self.hide_unknown_pay else None

    def prefilter(self, job: Job, *, ignore_age: bool = False) -> str | None:
        """Reason to drop the job, or None to keep it."""
        title = job.title or ""
        # scraped listings often only know the location after the job page is read (checked again in postfilter)
        if not (job.needs_detail and not job.location) and not self.location_ok(job):
            return "location"
        if any(rx.search(title) for rx in self.exclude):
            return "title-excluded"
        if not self._title_targeted(title):
            return "title-not-target"
        if not self.internships and (_INTERN_RX.search(title) or "intern" in (job.employment_type or "").lower()):
            return "internship"
        if reason := self.salary_reason(job, final=False):
            return reason
        return None if ignore_age else self._too_old(job)

    def _title_targeted(self, title: str) -> bool:
        return any(rx.search(title) for rx in self.include) or (
            any(rx.search(title) for rx in self.early) and bool(self.field_rx.search(title)))

    def _too_old(self, job: Job) -> str | None:
        age = days_ago(job.posted_at)
        if self.max_age and age is not None and age > int(self.max_age):
            return "too-old"
        return None

    # ------------------------------------------------------------ stage 2: needs the description
    def postfilter(self, job: Job, *, ignore_age: bool = False) -> str | None:
        text = job.description or ""
        if not ignore_age and self._too_old(job):  # detail pages can carry a more accurate (older) posting date
            return "too-old"
        if not self.location_ok(job):
            return "location"
        if any(rx.search(job.title or "") for rx in self.exclude):  # detail pages can refine the title
            return "title-excluded"
        if not self._title_targeted(job.title or ""):
            return "title-not-target"
        if not self.internships and (_INTERN_RX.search(job.title or "") or "intern" in (job.employment_type or "").lower()):
            return "internship"
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
        title, text = job.title or "", job.description or ""
        score, signals = 40, []
        years = min_years_required(text)
        if self.early_candidate:
            if any(rx.search(title) for rx in self.early) or job.level_hint in _ENTRY_LEVELS:
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
            signals.append(f"pay {salary_label(job.salary, self.currency)}")
        age = days_ago(job.posted_at)
        if age is not None and age <= 3:
            score += 4
            signals.append("posted ≤3d ago")
        return min(score, 100), signals, years
