"""Salary parsing and display.

Sources give pay in many shapes: "₹ 2,00,000 - 2,40,000 /year" (Internshala), "₹8L – ₹15L" (Wellfound),
min/max fields per year or month (Unstop, Lever, remote boards), and free text such as "CTC: 6-8 LPA",
"25,000 per month" or "$90k - $120k". Every amount is stored as yearly pay in one canonical unit, INR lakhs
(LPA), as [min, max], or None when the posting doesn't say. That keeps the tracker comparable whatever the
currency. It is shown, and the floor is set, in your own currency (profile.yaml salary.currency): INR as LPA,
anything else in thousands a year ("$90k-120k").
"""

from __future__ import annotations

import re

# Rough INR per unit, only used to compare pay across currencies (e.g. a USD remote job against an LPA floor).
RATES = {"INR": 1.0, "USD": 88.0, "EUR": 100.0, "GBP": 115.0, "CAD": 63.0, "AUD": 57.0, "SGD": 67.0, "AED": 24.0,
         "NZD": 52.0, "CHF": 100.0, "SEK": 8.4, "PLN": 22.0, "JPY": 0.59, "ZAR": 4.9, "BRL": 16.0, "MXN": 4.9,
         "PHP": 1.5, "MYR": 20.0, "PKR": 0.31, "BDT": 0.73, "LKR": 0.29}
SYMBOLS = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥"}
_DOLLARS = {"USD", "CAD", "AUD", "SGD", "NZD"}  # a bare "$" means your own dollar when you use one of these
_DISPLAY = {"currency": "INR"}                    # set from profile.yaml by configure()
_CUR = r"₹|rs\.?|inr|us\$|a\$|c\$|s\$|\$|usd|cad|aud|sgd|€|eur|£|gbp"
_NUM = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT = r"k|l|lakhs?|lacs?|lpa|lakh|cr|crores?|m|mn|million|b|bn|billion"
_TRAIL = "|".join(c.lower() for c in RATES)  # a currency code after the amount: "86,250 USD"


def _token(i: int) -> str:
    return (rf"(?:(?P<c{i}>{_CUR})\s*)?(?P<n{i}>{_NUM})\s*(?P<u{i}>(?:{_UNIT})(?![a-z]))?"
            rf"(?:\s*(?P<t{i}>{_TRAIL})(?![a-z]))?")


# "6 - 8 LPA", "₹8L – ₹15L", "86,250 USD – 169,000 USD", and Shine's run-together "4.5L8L a year" / "8K12K a month"
_RANGE = re.compile(_token(1) + r"(?:(?:\s*(?:-|to|–|—)\s*|(?<=[a-z]))" + _token(2) + ")?", re.I)
_MONTH = re.compile(r"month|/\s*mo\b|\bp\.?\s?m\.?\b|monthly", re.I)
_HOUR = re.compile(r"hour|/\s*hr\b|hourly", re.I)
_YEAR = re.compile(r"year|annum|annual|\bp\.?\s?a\.?\b|\byr\b|lpa|ctc", re.I)
_KEYWORD = re.compile(r"salary|ctc|compensation|stipend|package|remuneration|pay ?(?:range|scale)|base pay|\blpa\b|"
                      r"per annum|lakhs? (?:per|p\.?a)|in[- ]hand|annually|per year|yearly", re.I)


def configure(profile: dict) -> None:
    """Show pay in the currency set in profile.yaml (salary.currency). Called once when settings load."""
    _DISPLAY["currency"] = currency_of(profile)


def currency_of(profile: dict | None) -> str:
    cur = str(((profile or {}).get("salary") or {}).get("currency") or "INR").upper()
    return cur if cur in RATES else "INR"


def floor_lpa(pay_cfg: dict | None, currency: str = "INR") -> float:
    """The pay floor in the canonical unit. salary.min is in your currency's display unit (LPA for INR, thousands a
    year otherwise); the older salary.min_lpa is always LPA."""
    pay_cfg = pay_cfg or {}
    if pay_cfg.get("min") not in (None, ""):
        value = float(pay_cfg["min"] or 0)
        return value if currency == "INR" else round(value * 1e3 * RATES[currency] / 1e5, 4)
    return float(pay_cfg.get("min_lpa") or 0)


def _currency(text: str | None, default: str = "INR") -> str:
    t = (text or "").lower()
    if t in ("₹", "rs", "rs.", "inr", "fa-rupee"):
        return "INR"
    if t in ("$", "fa-dollar"):
        return default if default in _DOLLARS else "USD"
    if t in ("usd", "us$"):
        return "USD"
    symbols = {"€": "EUR", "£": "GBP", "c$": "CAD", "a$": "AUD", "s$": "SGD"}
    return symbols.get(t) or (t.upper() if t.upper() in RATES else default)


def _amount(num: str, unit: str | None) -> float | None:
    value = float(num.replace(",", ""))
    unit = (unit or "").lower()
    if unit in ("cr", "crore", "crores", "m", "mn", "million", "b", "bn", "billion"):
        return None  # company revenue / funding, not pay
    if unit in ("l", "lakh", "lakhs", "lac", "lacs", "lpa"):
        return value * 1e5
    if unit == "k":
        return value * 1e3
    return value


def to_lpa(lo: float | None, hi: float | None, currency: str = "INR", period: str = "year") -> list[float] | None:
    """[min, max] LPA from amounts in a currency per year / month; None when it isn't usable pay."""
    vals = [float(v) for v in (lo, hi) if v not in (None, "", 0, "0")]
    if not vals or period == "hour":
        return None
    rate = RATES.get((currency or "INR").upper())
    if rate is None:
        return None
    mult = 12 if period == "month" else 1
    low, high = min(vals), max(vals)
    if currency.upper() != "INR" and period == "year" and high < 1000:
        return None  # "$20-40" is hourly pay mislabelled as yearly
    out = [round(low * mult * rate / 1e5, 2), round(high * mult * rate / 1e5, 2)]
    return out if 0.3 <= out[1] <= 1000 else None


def _period(text: str, m: re.Match, unit: str) -> str | None:
    """year / month / hour from the words right next to the amount, never across a sentence boundary."""
    if unit in ("l", "lakh", "lakhs", "lac", "lacs", "lpa"):
        return "year"  # lakhs are always quoted per annum
    after = re.split(r"[.;\n](?:\s|$)|\n", text[m.end(): m.end() + 30])[0]
    before = re.split(r"[.;\n](?:\s|$)|\n", text[max(0, m.start() - 30): m.start()])[-1]
    for chunk in (after, before):
        if _HOUR.search(chunk):
            return "hour"
        if _MONTH.search(chunk):
            return "month"
        if _YEAR.search(chunk):
            return "year"
    return None


def parse_text(text: str, *, default_currency: str = "INR", need_signal: bool = False) -> list[float] | None:
    """Parse a pay string: the first amount that reads as pay. need_signal=True (free text): only accept amounts
    with a currency, a unit, or thousands grouping next to a pay period."""
    return next(_amounts(text, default_currency, need_signal), None)


def _amounts(text: str, default_currency: str, need_signal: bool):
    """Every amount in the text that reads as pay, as [min, max] LPA, in order."""
    text = (text or "").replace(" ", " ")
    for m in _RANGE.finditer(text):
        cur = m.group("c1") or m.group("c2") or m.group("t1") or m.group("t2")
        u1, u2 = m.group("u1"), m.group("u2")
        indian_grouping = bool(re.search(r"\d,\d{2},\d{3}", m.group(0)))
        # "100,000 - 120,000 per year": thousands-grouped amounts next to a pay period count without a currency
        grouped_pay = bool(re.search(r"\d,\d{3}(?!\d)", m.group(0))) and _period(text, m, "") in ("year", "month")
        if need_signal and not (cur or u1 or u2 or indian_grouping or grouped_pay):
            continue
        if not (cur or u1 or u2 or indian_grouping or m.group("n2")) and float(m.group("n1").replace(",", "")) < 1000:
            continue  # a bare small number ("2 years", "Level 3") is not pay
        a1 = _amount(m.group("n1"), u1 or (u2 if m.group("n2") and not u1 else None))
        a2 = _amount(m.group("n2"), u2) if m.group("n2") else None
        if a1 is None or (m.group("n2") and a2 is None):
            continue
        currency = _currency(cur, default_currency)
        period = _period(text, m, (u1 or u2 or "").lower())
        if period is None:  # no unit stated: Indian posts quote monthly pay in thousands, yearly in lakhs
            period = "month" if currency == "INR" and max(a1, a2 or 0) < 1e5 else "year"
        lpa = to_lpa(a1, a2, currency, period)
        if lpa:
            yield lpa


def in_description(text: str, default_currency: str = "INR") -> list[float] | None:
    """Pay mentioned in free text, only next to words like salary / CTC / LPA. Takes the highest mention, so a
    training stipend before the real CTC doesn't hide a job. Amounts with no currency are read as yours."""
    best = None
    for m in _KEYWORD.finditer(text or ""):
        window = text[max(0, m.start() - 40): m.end() + 90]
        for lpa in _amounts(window, default_currency, True):
            if best is None or lpa[1] > best[1]:
                best = lpa
    return best


def from_schema(base_salary) -> list[float] | None:
    """schema.org JobPosting.baseSalary (MonetaryAmount). Zero means 'not disclosed' on most boards."""
    if not isinstance(base_salary, dict):
        return parse_text(str(base_salary)) if base_salary else None
    value = base_salary.get("value")
    currency = base_salary.get("currency") or "INR"
    if isinstance(value, dict):
        unit = str(value.get("unitText") or "YEAR").upper()
        period = {"MONTH": "month", "HOUR": "hour", "WEEK": "hour", "DAY": "hour"}.get(unit, "year")
        lo = value.get("minValue", value.get("value"))
        hi = value.get("maxValue", value.get("value"))
        try:
            return to_lpa(float(lo or 0), float(hi or 0), currency, period)
        except (TypeError, ValueError):
            return None
    try:
        return to_lpa(float(value or 0), None, currency, "year")
    except (TypeError, ValueError):
        return None


def unit(currency: str | None = None) -> str:
    """The display unit: 'LPA' for INR, else e.g. 'USD k' (thousands a year)."""
    cur = currency or _DISPLAY["currency"]
    return "LPA" if cur == "INR" else f"{cur} k"


def in_units(sal: list[float] | None, currency: str | None = None) -> tuple[float, float] | None:
    """[min, max] in the display unit of a currency (LPA for INR, thousands a year otherwise)."""
    if not sal:
        return None
    cur = currency or _DISPLAY["currency"]
    if cur == "INR":
        return float(sal[0]), float(sal[1])
    conv = lambda v: v * 1e5 / RATES[cur] / 1e3
    return conv(sal[0]), conv(sal[1])


def _fmt(v: float, cur: str) -> str:
    """LPA keeps one decimal (17.6); thousands of other currencies are whole above 10 ($95k, not $95.3k)."""
    return f"{v:.1f}".rstrip("0").rstrip(".") if cur == "INR" or v < 10 else f"{round(v):d}"


def short(sal: list[float] | None, currency: str | None = None) -> str:
    """'6-9' (LPA) or '90-120' (USD k): numbers only, for table columns headed by unit()."""
    cur = currency or _DISPLAY["currency"]
    vals = in_units(sal, cur)
    if not vals:
        return ""
    lo, hi = (_fmt(v, cur) for v in vals)
    return lo if lo == hi else f"{lo}-{hi}"


def label(sal: list[float] | None, currency: str | None = None) -> str:
    """'₹6-9 LPA', '$90k-120k' or 'CAD 70k-90k'; '' when unknown."""
    cur = currency or _DISPLAY["currency"]
    nums = short(sal, cur)
    if not nums:
        return ""
    if cur == "INR":
        return f"₹{nums} LPA"
    lo, _, hi = nums.partition("-")
    text = f"{lo}k-{hi}k" if hi else f"{lo}k"
    return f"{SYMBOLS[cur]}{text}" if cur in SYMBOLS else f"{cur} {text}"
