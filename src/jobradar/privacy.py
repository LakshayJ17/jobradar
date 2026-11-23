"""Remove personal details from resume text before it goes to Gemini.

Google's terms for the free (unpaid) Gemini API say prompts may be used to improve Google's products, that human
reviewers may read them, and that you should not submit personal information. Matching a job needs your skills,
projects and education, not your phone number. So contact details become placeholders such as [EMAIL] and
[PHONE] before any AI call. Drafts swap the real values back in locally, after the AI has replied.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# "name [at] gmail [dot] com", "name(at)gmail.com", "name at gmail dot com", "name @ gmail.com"
_EMAIL_LOOSE = re.compile(
    r"[\w.+-]+\s*(?:\[at\]|\(at\)|\{at\})\s*[\w-]+(?:\s*(?:\.|\[dot\]|\(dot\)|\{dot\})\s*[\w-]+)+"
    r"|[\w.+-]+\s+at\s+[\w-]+(?:\s+dot\s+[\w-]+)+"
    r"|[\w.+-]+\s+@\s*[\w-]+(?:\.[\w-]+)+|[\w.+-]+@\s+[\w-]+(?:\.[\w-]+)+", re.I)
_PROFILE_HOSTS = (r"linkedin\.com|github\.com|gitlab\.com|leetcode\.com|codeforces\.com|codechef\.com|kaggle\.com|"
                  r"hackerrank\.com|geeksforgeeks\.org|medium\.com|x\.com|twitter\.com|behance\.net|dribbble\.com|"
                  r"huggingface\.co|stackoverflow\.com|topmate\.io|linktr\.ee")
_HOSTED = r"github\.io|vercel\.app|netlify\.app|herokuapp\.com|onrender\.com|pages\.dev|web\.app|firebaseapp\.com"
_URL = re.compile(
    r"https?://[^\s|,;<>()\[\]]+"                                         # anything with a scheme
    r"|\bwww\.[^\s|,;<>()\[\]]+"                                          # www.
    rf"|(?<![\w.@])(?:[\w-]+\.)?(?:{_PROFILE_HOSTS})/[^\s|,;<>()\[\]]+"   # linkedin.com/in/you, github.com/you
    rf"|(?<![\w.@])[\w-]+(?:\.[\w-]+)*\.(?:{_HOSTED})(?:/[^\s|,;<>()\[\]]*)?",  # you.github.io, app.vercel.app
    re.I)
# Indian mobiles (98765 43210, 987-654-3210, +91-9876543210, 09876543210) and any "+<country>" number.
_PHONE_IN = re.compile(r"(?<![\w+])(?:\(?\+?91\)?[\s.-]*|0)?[6-9]\d{2}(?:\d{2}[\s.-]?\d{5}|[\s.-]\d{3}[\s.-]\d{4})(?!\d)")
_PHONE_INTL = re.compile(r"(?<![\w+])\+\d[\d\s().-]{6,16}\d(?!\d)")
# 10-digit mobiles with any single separators ("98 7654 3210", "(98765) 43210"), landlines ("011-2345 6789"),
# US-style "(415) 555-0100"
_PHONE_LOOSE = re.compile(r"(?<![\w+])(?:\(?[6-9]\d{4}\)?[\s.-]?\d{5}|[6-9](?:[\s.-]?\d){9})(?!\d)"
                          r"|(?<![\w+])0\d{2,4}[\s-]\d{3,4}[\s-]?\d{4}(?!\d)"
                          r"|(?<![\w+])\(\d{3}\)\s?\d{3}[\s.-]\d{4}(?!\d)|(?<![\w+(])\d{3}[.-]\d{3}[.-]\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
# "LinkedIn: priya-sharma-123", "GitHub - priyasharma": a profile handle written without its URL
_LABELLED = re.compile(r"\b(linkedin|github|gitlab|leetcode|codeforces|codechef|kaggle|hackerrank|twitter|instagram|"
                       r"telegram|medium|behance|dribbble|portfolio)(\s*(?:id|handle|username)?\s*[:\-–]\s*)"
                       r"(?!\[)([A-Za-z0-9][\w.-]{2,40})", re.I)
_HANDLE = re.compile(r"(?<![\w.@/])@[A-Za-z0-9_]{3,30}(?![\w/@.])")
_PERSONAL_SITE = re.compile(r"(?<![\w.@/-])[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:dev|me|online|site|tech|xyz|page|live|website|"
                            r"codes|blog|space|studio|digital|bio)(?:/[^\s|,;<>()\[\]]*)?(?![\w.])")
_DOB = re.compile(r"\bborn(?:\s+on)?\s*[:\-]?\s*\d{1,2}(?:st|nd|rd|th)?[\s/.-]+(?:\d{1,2}|[A-Za-z]{3,9})[\s/.,-]+\d{2,4}",
                  re.I)
_PIN_LOOSE = re.compile(r"(?<=[A-Za-z])([,\s]\s?)([1-9]\d{2}\s?\d{3})(?![\d.%])")
_ADDRESS = re.compile(r"\b(?:road|rd\.|marg|nagar|sector|street|lane|colony|apartments?|flat no|house no|h\.? ?no|"
                      r"block [a-z0-9]|phase [0-9ivx]+|district|tehsil|village|p\.?o\.? box)\b", re.I)
_INVISIBLE = re.compile("[\u200b-\u200f\u2060-\u2064\ufeff\u00ad]")
_PIN = re.compile(r"([,\-–]\s?)([1-9]\d{2}\s?\d{3})(?!\d)")  # "New Delhi - 110085"
_AADHAAR = re.compile(r"(?<![\d.])(?:\d{4}[\s.-]\d{4}[\s.-]\d{4}|[2-9]\d{11})(?![\d.])")
# "Key: value" lines with personal details, common on Indian resumes, plus the closing declaration.
_PERSONAL_LINE = re.compile(
    r"^\W*(?:(?:(?:permanent|current|residential|home|correspondence) )?address\b|d\.?o\.?b\b|date of birth|"
    r"birth ?date|father'?s? name|mother'?s? name|marital status|nationality|religion|aadhaa?r|passport|"
    r"blood group|declaration\b|i hereby declare|born\b"
    r"|(?:age|gender|sex|caste|category|pan(?: no\.?| card| number)?|place|date|pin ?code|postal code|guardian|"
    r"father|mother|hobbies|interests|languages known)\s*[:\-–])",
    re.I)
_HEADING = re.compile(
    r"^\W*(?:education(?:al)?(?: qualifications?| details| background)?|academics?|academic (?:details|profile)|"
    r"(?:technical |key |core )?skills?(?: & tools| and tools| summary)?|technologies|tech stack|"
    r"(?:professional |career )?summary|(?:career |professional )?objective|profile|about(?: me)?|"
    r"(?:work |professional )?experience|internships?(?: experience)?|employment|"
    r"(?:academic |personal |key )?projects?|certifications?|achievements?|publications?|"
    r"positions? of responsibility|extra[- ]?curricular(?: activities)?|coursework|relevant coursework|"
    r"academic qualifications?|educational qualifications?|qualifications?|work history|employment history|"
    r"professional history|career history|technical proficienc(?:y|ies)|trainings?|honou?rs(?: and awards)?|awards|"
    r"volunteer(?:ing)?(?: experience)?|leadership(?: experience)?|languages|interests|hobbies)\W*$",
    re.I)
_NAME_LINE = re.compile(r"^[^\W\d_][^\W\d_.'-]*(?:[.'-][^\W\d_]+)*\.?(?:\s+[^\W\d_][^\W\d_.'-]*(?:[.'-][^\W\d_]+)*\.?){1,3}$")
_NOT_A_NAME = re.compile(r"^(?:resume|r[ée]sum[ée]|curriculum vitae|cv|bio[- ]?data|profile)$", re.I)


@dataclass
class Redacted:
    text: str
    values: dict[str, str] = field(default_factory=dict)   # placeholder -> original (kept on this machine only)
    removed: list[str] = field(default_factory=list)       # what kinds were removed, for display (no values)

    def restore(self, text: str) -> str:
        """Put real values back into AI output (drafts), longest placeholder first."""
        for placeholder in sorted(self.values, key=len, reverse=True):
            text = text.replace(placeholder, self.values[placeholder])
        return text


def _link_label(url: str) -> str:
    low = url.lower()
    for host, label in (("linkedin.", "LINKEDIN"), ("github.com", "GITHUB"), ("leetcode.", "LEETCODE"),
                        ("kaggle.", "KAGGLE"), ("codeforces.", "CODEFORCES"), ("codechef.", "CODECHEF")):
        if host in low:
            return label
    return "LINK"


def redact(text: str, profile: dict | None = None, *, header: bool = True, names: list[str] | None = None) -> Redacted:
    """header=False for text that isn't a whole resume (prompt fragments): only the inline patterns apply.
    names: more names to hide (e.g. the one in profile/resume.yaml)."""
    profile = profile or {}
    out = Redacted(text="")
    seen: dict[str, str] = {}  # original -> placeholder
    counts: dict[str, int] = {}

    def placeholder(kind: str, value: str) -> str:
        value = value.strip()
        if value in seen:
            return seen[value]
        counts[kind] = counts.get(kind, 0) + 1
        ph = f"[{kind}]" if counts[kind] == 1 else f"[{kind}-{counts[kind]}]"
        seen[value] = ph
        out.values[ph] = value
        return ph

    # Fullwidth / styled characters become plain ones and invisible characters go, so "９８７６５" or "a\u200b@b.com"
    # can't slip past the patterns below.
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text or ""))
    text = "".join(str(unicodedata.decimal(c)) if c.isdecimal() and not c.isascii() else c for c in text)  # ९८ -> 98
    lines = text.splitlines()

    # The name: from profile.yaml, else the first line that looks like one ("Priya Sharma", "Priya Sharma |
    # Developer", "José Fernández García"), skipping a "RESUME" title line.
    names = [unicodedata.normalize("NFKC", str(n or "")).strip() for n in [profile.get("name"), *(names or [])]]
    for ln in [ln.strip() for ln in lines if ln.strip()][:3] if header else []:
        if _NOT_A_NAME.match(ln):
            continue
        part = re.split(r"\s*(?:[|•·,]|\s[-–—]\s)\s*", ln)[0].strip()
        if _NAME_LINE.match(part) and len(part) <= 40 and not _HEADING.match(part):
            names.append(part)
        break
    names = [n for n in dict.fromkeys(names) if len(n) >= 3 and not _NOT_A_NAME.match(n)]
    for full in list(names):  # "Priya Sharma" is also "P. Sharma", "Priya S." and "Sharma Priya"
        parts = full.split()
        if len(parts) >= 2 and all(len(x) > 1 for x in (parts[0], parts[-1])):
            names += [f"{parts[0][0]}. {parts[-1]}", f"{parts[0][0]}.{parts[-1]}", f"{parts[0]} {parts[-1][0]}.",
                      f"{parts[-1]} {parts[0]}"]
    names = list(dict.fromkeys(names))
    if names:
        out.values["[NAME]"] = names[0]
        seen[names[0]] = "[NAME]"

    def scrub(ln: str) -> str:
        ln = _EMAIL.sub(lambda m: placeholder("EMAIL", m.group()), ln)
        ln = _EMAIL_LOOSE.sub(lambda m: placeholder("EMAIL", m.group()), ln)
        ln = _URL.sub(lambda m: placeholder(_link_label(m.group()), m.group()), ln)
        ln = _PERSONAL_SITE.sub(lambda m: placeholder("LINK", m.group()), ln)
        ln = _PHONE_IN.sub(lambda m: placeholder("PHONE", m.group()), ln)
        ln = _PHONE_INTL.sub(lambda m: placeholder("PHONE", m.group())
                             if 8 <= len(re.sub(r"\D", "", m.group())) <= 15 else m.group(), ln)
        ln = _PHONE_LOOSE.sub(lambda m: placeholder("PHONE", m.group()), ln)
        ln = _PIN.sub(r"\1[PIN]", ln)
        ln = _PIN_LOOSE.sub(r"\1[PIN]", ln)
        ln = _AADHAAR.sub("[ID]", ln)
        ln = _PAN.sub("[ID]", ln)
        ln = _DOB.sub("[DOB]", ln)
        ln = _HANDLE.sub(lambda m: placeholder("HANDLE", m.group()), ln)
        ln = _LABELLED.sub(lambda m: m.group(1) + m.group(2) + placeholder(
            m.group(1).upper() if m.group(1).lower() in ("linkedin", "github", "leetcode", "kaggle", "codeforces",
                                                         "codechef") else "LINK", m.group(3)), ln)
        for name in names:
            ln = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "[NAME]", ln, flags=re.I)
        return ln

    # The header block above the first section heading is name + contact + address. Its emails, phones and links
    # become placeholders on one "Contact:" line and the rest is dropped. Only done when a heading is near the top.
    start = next((i for i, ln in enumerate(lines[:15]) if _HEADING.match(ln.strip()) and len(ln.strip()) < 45), None)
    body = lines
    if not header:
        pass
    elif start:
        for ln in lines[:start]:
            scrub(ln)
        kept = [ph for ph in out.values if ph != "[NAME]"]
        body = (["Contact: " + " | ".join(kept)] if kept else []) + lines[start:]
        out.removed.append(f"header block ({start} lines: name, contact, address)")
    else:  # no heading near the top: still drop address-like lines from the first few
        top = [i for i, ln in enumerate(lines[:8]) if _ADDRESS.search(ln) and len(ln) < 120]
        if top:
            body = [ln for i, ln in enumerate(lines) if i not in top]
            out.removed.append(f"{len(top)} address line(s) near the top")

    cleaned = []
    dropped_personal = 0
    for ln in body:
        if _PERSONAL_LINE.match(ln):
            dropped_personal += 1
            continue
        cleaned.append(scrub(ln).rstrip())
    if dropped_personal:
        out.removed.append(f"{dropped_personal} personal-detail lines (address, DOB, family, declaration…)")
    kinds = sorted({re.sub(r"-\d+$", "", ph.strip("[]")).lower() for ph in out.values})
    if kinds:
        out.removed.append("replaced with placeholders: " + ", ".join(kinds))
    out.text = re.sub(r"\n{3,}", "\n\n", "\n".join(cleaned)).strip()
    return out


def extraction_warnings(text: str) -> list[str]:
    """Spot resumes whose PDF text did not extract cleanly (image-only PDFs, run-together words)."""
    words = text.split()
    if len(text.strip()) < 300:
        return ["very little text came out of the file: is it a scanned / image-only PDF? Export a text PDF "
                "or save profile/resume.txt"]
    warnings = []
    if sum(len(w) > 25 for w in words) / len(words) > 0.03:
        warnings.append("many words look run together (common with two-column or Canva PDFs): check "
                        "`jobradar resume`, or save a plain profile/resume.txt instead")
    if sum(len(w) == 1 for w in words) / len(words) > 0.3:
        warnings.append("letters look spaced out (P y t h o n): save a plain profile/resume.txt instead")
    return warnings
