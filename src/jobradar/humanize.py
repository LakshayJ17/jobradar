"""Catch writing that reads as AI-generated, in resume bullets and outreach messages.

Hiring managers now name heavily AI-written applications as a top red flag (Resume Genius 2026: 49% of 1,500
managers), and recruiters list the same tells: stock verbs ("spearheaded", "leveraged", "orchestrated"), em dashes,
stock openers ("I hope this message finds you well"), every bullet built the same way, ", resulting in..." tails,
invented numbers, and identical phrasing across applicants. No single word proves anything, so jobradar treats these
as things to avoid, not a detector: the AI is told to avoid them, this module checks in code, mechanical problems
are fixed, the rest goes back to the AI once, and anything still left is shown to you.

Sources: simplify.jobs/blog/can-recruiters-tell-if-ai-wrote-your-resume, reed.com/articles/seven-ways-to-spot-ai-on-
a-cv-or-covering-letter, enhancv.com/blog/signs-of-ai-generated-resume, en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_
writing, pangram.com/resources/most-common-ai-phrases, and Reinhart et al. (PNAS 2025) on participial clauses.
"""

from __future__ import annotations

import re

# Resume words that read as AI-written or as filler. A rewrite may never introduce them.
RESUME_ALWAYS = """
spearheaded|spearheading|orchestrated|orchestrating|leveraged|leveraging|leverage|synergy|synergistic|fostered|
fostering|transformative|deep dive|vibrant|game-changer|game changer|game-changing|passionate|passion for|
driven by a passion|seamless|seamlessly|cutting-edge|cutting edge|dynamic|empowered|empowering|visionary|
multifaceted|unwavering|thought leader|at the intersection of|results-driven|results-oriented|detail-oriented|
self-starter|go-getter|proven track record|track record of|proven ability|extensive experience|hard-working|
hardworking|team player|excellent communicator|strong communication skills|tech-savvy|adept|delve|delved|tapestry|
testament to|pivotal|meticulous|meticulously|honed|utilized|utilize|utilizing|responsible for|operational excellence|
delivering exceptional|committed to delivering|committed to fostering|committed to continuous|eager to leverage|
fast-paced environment|highly motivated|motivated individual|world-class|best-in-class|state-of-the-art|
revolutionized|championed|elevated|harnessed|harnessing|navigated the complexities|ever-evolving|evolving landscape|
realm|showcasing|showcased|underscore|underscored|bolstered|garnered|paramount|holistic|actionable insights|
valuable insights|instrumental in|successfully|innovative solutions|references available upon request|
i hereby declare
"""
# Fine once in a while, flagged when they pile up (more than one each, or three in all).
RESUME_SOMETIMES = """
streamlined|optimized|enhanced|robust|scalable|cross-functional|collaborated|data-driven|innovative|dedicated|
reliable|proactive|key|crucial|ensured|facilitated|end-to-end|stakeholders|best practices|problem solver|
attention to detail|continuous improvement|operational efficiency|while maintaining|resulting in|leading to|ensuring|
significantly|effectively|efficiently|comprehensive|various|actively|helped|contributed to|assisted in|worked on|
pioneered
"""
MESSAGE_ALWAYS = """
i hope this message finds you well|i hope this email finds you well|hope this finds you well|i am writing to express|
i'm writing to express|i am writing to inquire|i am reaching out to express|i came across your profile|
came across your profile|i stumbled upon|i'd love to connect|i would love to connect|pick your brain|delve|
aligns with|aligns perfectly|align with my|resonate|resonates with|resonated with me|i am excited to|i'm excited to|
thrilled to|passionate about|keen interest|unique blend|eager to contribute|valuable addition|meaningful impact|
make an impact|i am confident that|i'm confident that|leverage my|perfect fit|ideal candidate|dynamic team|
cutting-edge|truly inspiring|your impressive|i was impressed by|your journey|in today's fast-paced|ever-evolving|
navigate the|tapestry|testament to|seamlessly|synergy|elevate|foster|showcase|underscore|invaluable|
at your earliest convenience|please do not hesitate|don't hesitate to reach out|to whom it may concern|
dear sir/madam|dear sir or madam|respected sir|respected ma'am|greetings of the day|just checking in|
just circling back|touching base|per my last email|not sure if you saw|i hope this helps|let me know if you'd like|
certainly|furthermore|moreover|in conclusion|do the needful|revert back|esteemed
"""
MESSAGE_SOMETIMES = """
hope you're doing well|i'm reaching out because|thank you for your time and consideration|
looking forward to hearing from you|kindly|additionally|excited|genuinely|truly|really|great fit|best regards
"""
# Technical phrases that contain a listed word but are fine ("dynamic programming" is a DSA topic).
WHITELIST = """
dynamic programming|dynamic routing|dynamic typing|dynamic pricing|dynamic imports?|dynamic content|elevated privileges|
elevated permissions|seamless rotation|robust statistics|api keys?|primary keys?|foreign keys?|key-value|key value|
keyboard|keyword|ssh keys?|key rotation|keys
"""
PARTICIPIAL_TAIL = re.compile(r",\s*(?:thereby\s+)?(?:resulting in|leading to|ensuring|enabling|driving|highlighting|"
                              r"showcasing|demonstrating|contributing to|reflecting)\b", re.I)
# Your own details travel as [NAME], [EMAIL]... and are filled in locally; "[first name]" is the recipient's, for you.
_OURS = r"(?:first name|NAME|EMAIL|PHONE|LINKEDIN|GITHUB|LEETCODE|KAGGLE|CODEFORCES|CODECHEF|LINK)(?:-\d+)?"
PLACEHOLDER = re.compile(rf"\[(?!{_OURS}\])[^\]\n]{{1,30}}\]|\{{[^}}\n]{{1,30}}\}}|\bXX+\b|add numbers|"
                         r"insert (?:metric|number)", re.I)
PRONOUN = re.compile(r"\b(?:I|me|my|we|our|us)\b")
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF️]")
_FLATTERY = re.compile(r"\byour\s+(?:\w+\s+){0,2}?(?:impressive|inspiring|amazing|incredible|remarkable|stellar)\b|"
                       r"\b(?:impressive|inspiring|amazing|incredible|remarkable)\s+(?:\w+\s+){0,1}?(?:work|journey|"
                       r"profile|career|achievements)\b", re.I)
CONTRACTION = re.compile(r"\b\w+'(?:m|re|s|ve|d|ll|t)\b", re.I)


def _rx(words: str) -> re.Pattern:
    alts = sorted({re.escape(w.strip()) for w in words.replace("\n", "").split("|") if w.strip()}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(alts) + r")(?![\w-])", re.I)


_RESUME_ALWAYS, _RESUME_SOMETIMES = _rx(RESUME_ALWAYS), _rx(RESUME_SOMETIMES)
_MESSAGE_ALWAYS, _MESSAGE_SOMETIMES = _rx(MESSAGE_ALWAYS), _rx(MESSAGE_SOMETIMES)
_WHITELIST = _rx(WHITELIST)


def _clean(text: str) -> str:
    return _WHITELIST.sub(" ", (text or "").replace("\u2019", "'"))


def stock_phrases(text: str, kind: str = "resume") -> tuple[list[str], list[str]]:
    """(always-replace hits, sometimes hits) found in text."""
    text = _clean(text)
    if kind == "resume":
        always = _RESUME_ALWAYS.findall(text)
        sometimes = _RESUME_SOMETIMES.findall(text)
    else:
        always = _MESSAGE_ALWAYS.findall(text) + _RESUME_ALWAYS.findall(text)
        sometimes = _MESSAGE_SOMETIMES.findall(text) + _RESUME_SOMETIMES.findall(text)
    return [w.lower() for w in always], [w.lower() for w in sometimes]


def sentences(text: str) -> list[str]:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out = []
    for ln in lines:
        out += [s.strip() for s in re.split(r"(?<=[.?!])\s+(?=[A-Z\[])", ln) if len(s.strip()) > 2]
    return out


def tells(text: str, kind: str = "message") -> list[str]:
    """What makes this text read as AI-written. kind: 'resume' (one bullet) or 'message'."""
    text = text or ""
    out = []
    always, sometimes = stock_phrases(text, kind)
    if always:
        out.append("stock phrases: " + ", ".join(f'"{w}"' for w in dict.fromkeys(always)))
    piled = [w for w in dict.fromkeys(sometimes) if sometimes.count(w) > 1]
    if piled or len(sometimes) > (1 if kind == "resume" else 2):
        out.append("filler words pile up: " + ", ".join(f'"{w}"' for w in dict.fromkeys(sometimes)))
    if "\u2014" in text or re.search(r"\w \u2013 \w", text) or " -- " in text:
        out.append("em dash")
    if _EMOJI.search(text):
        out.append("emoji")
    gap = PLACEHOLDER.search(text)
    if gap:
        out.append(f"leftover placeholder: {gap.group(0)}")
    if re.search(r"\*\*|^#{1,6}\s|__", text, re.M):
        out.append("markdown formatting")
    if re.search(r"\bnot only\b[^.]*\bbut also\b|\bit'?s not just\b[^.]*\bit'?s\b", text, re.I):
        out.append('"not only ... but also" pattern')
    if kind == "resume":
        if PARTICIPIAL_TAIL.search(text):
            out.append('", resulting in / ensuring ..." tail')
        if PRONOUN.search(text):
            out.append("I / my / we in a resume bullet")
        if len(text.split()) > 30:
            out.append("longer than 30 words")
        return out
    if "!" in text:
        out.append("exclamation mark")
    if text.count(";") > 1:
        out.append("several semicolons")
    if text.count("?") > 2:
        out.append("more than two questions: keep to one ask")
    sents = sentences(text)
    long = [s for s in sents if len(s.split()) > 25]
    if long:
        out.append(f"sentence over 25 words: \"{' '.join(long[0].split()[:8])}...\"")
    openers = [s.split()[0].lower().strip(",") for s in sents if s.split()]
    if any(a == b and a not in ("hi", "hello", "thanks") for a, b in zip(openers, openers[1:])):
        out.append("two sentences in a row start with the same word")
    if len(openers) >= 4 and openers.count("i") / len(openers) > 0.6:
        out.append('most sentences start with "I"')
    if len(text.split()) > 40 and not CONTRACTION.search(text):
        out.append("no contractions (I'm, I've): reads stiff")
    if _FLATTERY.search(text):
        out.append("flattery")
    if len(re.findall(r"\b[\w.+#-]+, [\w.+#-]+,? and [\w.+#-]+\b", text)) > 2:
        out.append("too many lists of three")
    return out


def fix(text: str) -> str:
    """Mechanical fixes: dashes used as pauses become commas, emoji and markdown go, doubled punctuation goes."""
    from .util import clean_block

    text = clean_block(text).strip()
    text = re.sub(r"\s*\u2014\s*", ", ", text)               # an em dash between words becomes a comma
    text = re.sub(r"(?<=[a-z]) \u2013 (?=[a-z])", ", ", text)  # so does a spaced en dash between words (not in dates)
    text = re.sub(r"\s+--\s+", ", ", text)
    text = _EMOJI.sub("", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"!+", ".", text) if text.count("!") else text
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r",\.", ".", text)
    return text.strip()


def resume_notes(bullets: list[str]) -> list[str]:
    """Tips on the whole resume (your own text, which jobradar never rewrites wholesale)."""
    notes = []
    always = [w for b in bullets for w in stock_phrases(b, "resume")[0]]
    if always:
        notes.append("words recruiters read as AI or filler: " + ", ".join(f'"{w}"' for w in dict.fromkeys(always)))
    verbs = [b.split()[0].lower().strip(",.") for b in bullets if b.split()]
    repeated = [v for v in dict.fromkeys(verbs) if verbs.count(v) > 2]
    if repeated:
        notes.append("bullets starting with the same verb more than twice: "
                     + ", ".join(f'"{v.capitalize()}" x{verbs.count(v)}' for v in repeated)
                     + " (vary a couple: built, wrote, added, set up, moved, fixed)")
    if len(bullets) >= 4 and all(re.search(r"\d", b) for b in bullets):
        notes.append("every bullet has a number, which can look invented: keep only the ones you can prove")
    tails = [b for b in bullets if PARTICIPIAL_TAIL.search(b)]
    if len(bullets) >= 3 and len(tails) / len(bullets) > 0.3:
        notes.append(f'{len(tails)} bullets end with ", resulting in / ensuring ...": a common AI pattern')
    if any(PRONOUN.search(b) for b in bullets):
        notes.append("drop I / my / we from bullets")
    if any("\u2014" in b for b in bullets):
        notes.append("em dashes: use commas or a plain hyphen")
    long = [b for b in bullets if len(b.split()) > 30]
    if long:
        notes.append(f"{len(long)} bullet(s) over 30 words: split or trim")
    lengths = [len(b.split()) for b in bullets]
    if len(lengths) >= 5:
        mean = sum(lengths) / len(lengths)
        cv = (sum((x - mean) ** 2 for x in lengths) / len(lengths)) ** 0.5 / mean if mean else 1
        if cv < 0.25:
            notes.append("bullets are all about the same length: real resumes mix short and long ones")
    return notes


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>")


def md_text(text) -> str:
    """AI-written text placed in a markdown file outside a code block: plain words only. No images (a preview would
    load them, which can leak data in the URL), no links or HTML, no line breaks."""
    from .util import clean_line

    text = clean_line(text)
    text = _MD_IMAGE.sub("", text)
    text = _MD_LINK.sub(lambda m: m.group(1), text)
    text = _AUTOLINK.sub(lambda m: m.group(1), text)
    return text.replace("<", "&lt;").replace(">", "&gt;").replace("`", "'")
