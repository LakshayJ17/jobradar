"""`jobradar draft <id>`: an application kit for one job, in data/drafts/<id>-<company>-<role>/.

* <Your_Name>_Resume.pdf / .docx / .txt: your resume tailored to this job (see tailor.py for what may change)
* kit.md: why you fit, what changed in the resume and why, keywords you have and gaps, five messages, how to use them
* prep.md: interview prep with free resources (see prep.py)

With a free Gemini key the AI plans the tailoring, drafts the messages and the prep, and code checks all of it.
Without a key the same kit is made by rules and templates. Your contact details never go to the AI: it sees
[NAME], [EMAIL] placeholders that are filled in on this machine.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import cv as cvmod
from . import humanize, outreach, prep, resume_render, tailor
from .humanize import md_text
from .ai import AIError, AuthError, Gemini, candidate, conform, fence
from .config import Settings, resume_text
from .privacy import redact
from .report import posted_label
from .util import clean_block, clean_line, clip, safe_url, today


class KitError(Exception):
    pass


@dataclass
class Kit:
    folder: Path
    files: list[Path] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # what was done how (AI / rules), shown after the run
    problems: list[str] = field(default_factory=list)   # things you should look at before sending


def cv_path(settings: Settings) -> Path:
    return settings.root / "profile" / cvmod.CV_FILE


def kit_folder(settings: Settings, rec: dict) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", f"{rec['company']}-{rec['title']}".lower()).strip("-")[:60] or "job"
    return settings.drafts_dir / f"{int(rec['id'])}-{slug}"


def _gemini(settings: Settings, key: str) -> Gemini:
    ai = settings.profile.get("ai") or {}
    models = list(dict.fromkeys((ai.get("draft_models") or []) + (ai.get("models") or [])))
    return Gemini(key, models, rpm=int(ai.get("rpm", 10)))


def _job_block(rec: dict, limit: int = 6000) -> str:
    return (f"<job>\nCompany: {fence(rec['company'], 120)}\nTitle: {fence(rec['title'], 200)}\n"
            f"Location: {fence(rec.get('location') or '', 120)}\n"
            f"Description:\n{fence(rec.get('description') or '(no description available)', limit)}\n</job>")


def _restore(value, private):
    if isinstance(value, str):
        return private.restore(value)
    if isinstance(value, list):
        return [_restore(v, private) for v in value]
    if isinstance(value, dict):
        return {k: _restore(v, private) for k, v in value.items()}
    return value


async def setup_cv(settings: Settings, gemini: Gemini, *, log=print) -> tuple[dict, list[str]]:
    """Build profile/resume.yaml from the resume file (AI sorts the redacted text into sections)."""
    text, source = resume_text(settings)
    path = settings.root / source
    if not path.exists():
        raise KitError("no resume file found in profile/: add your resume (.pdf, .txt or .md) first")
    private = redact(text, settings.profile)
    log(f"reading {source} into profile/{cvmod.CV_FILE} (the AI sees it without your contact details)…")
    data, model = await cvmod.extract(private.text, gemini)
    cv, warnings = cvmod.build(data, text, private, cvmod.pdf_links(path))
    cvmod.save(cv, cv_path(settings), source)
    return cv, warnings


def _banned_sample() -> str:
    words = [w.strip() for w in humanize.RESUME_ALWAYS.replace("\n", "").split("|") if w.strip()]
    return ", ".join(words[:60])


async def build(settings: Settings, rec: dict, key: str | None, *, parts=("resume", "messages", "prep"),
                log=print) -> Kit:
    base = cvmod.load(cv_path(settings))
    gemini = _gemini(settings, key) if key else None
    try:
        if base is None:
            if not gemini:
                raise KitError(f"profile/{cvmod.CV_FILE} is missing. Run `jobradar resume --setup` (needs a free "
                               "GEMINI_API_KEY), or copy config/resume.example.yaml there and fill it in by hand")
            base, warnings = await setup_cv(settings, gemini, log=log)
            log(f"created profile/{cvmod.CV_FILE}: check it once (`jobradar resume --pdf`)")
            for w in warnings:
                log(f"  ! {w}")
        return await _build(settings, rec, base, gemini, parts, log)
    finally:
        if gemini:
            await gemini.aclose()


async def _build(settings, rec, base, gemini, parts, log) -> Kit:
    profile = settings.profile
    rec = dict(rec) | {k: clean_line(rec.get(k)) for k in ("company", "title", "location", "url", "source")} \
        | {"description": clean_block(rec.get("description"))}
    kit = Kit(folder=kit_folder(settings, rec))
    kit.folder.mkdir(parents=True, exist_ok=True)
    job_text = f"{rec['title']}\n{rec.get('description') or ''}"
    my_names = [base.get("name") or ""]
    body_private = redact(tailor.prompt_text(base), profile, header=False, names=my_names)
    contact_private = redact(resume_text(settings)[0], profile, names=my_names)  # [NAME]... -> you, locally

    def ai_note(what: str, model: str) -> None:
        kit.notes.append(f"{what}: {model}")

    # ---- 1. tailoring plan
    plan, report = None, None
    if gemini:
        try:
            system = tailor.TAILOR_SYSTEM.format(max_rewrites=tailor.MAX_REWRITES, banned=_banned_sample())
            log("planning the tailored resume…")
            plan, model = await gemini.generate_json(system, f"{body_private.text}\n\n{_job_block(rec)}",
                                                     tailor.TAILOR_SCHEMA, max_tokens=8192, temperature=0.3,
                                                     patient=False)
            plan = _restore(conform(plan, tailor.TAILOR_SCHEMA), body_private)  # shape checked here too
            ai_note("resume plan", model)
        except AuthError:
            raise
        except AIError as exc:
            kit.notes.append(f"resume plan: AI unavailable ({clip(str(exc), 120)}), reordered by keywords instead")
    if plan is None:
        plan = tailor.rule_plan(base, job_text)
        plan["keywords_missing"] = list((rec.get("ai") or {}).get("gaps") or [])
        if not gemini:
            kit.notes.append("resume plan: by keyword overlap (no rewording)")
    tailored, report = tailor.apply(base, plan)

    # ---- 2. resume files
    stem = resume_render.file_stem(base)
    if "resume" in parts:
        src = cvmod.resume_source_path(settings)
        max_pages = 1
        if src and src.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            max_pages = max(1, min(2, len(PdfReader(str(src)).pages)))
        style = (profile.get("resume") or {}).get("style") or {}
        pdf, used, removed = resume_render.fit(tailored, max_pages, style,
                                               tailor.cutters(tailored, base, report["cut_first"]))
        report["trimmed"] = removed
        if resume_render.page_count(pdf) > max_pages:
            kit.problems.append(f"the resume is still over {max_pages} page(s): trim profile/{cvmod.CV_FILE}")
        for path, write in ((kit.folder / f"{stem}.pdf", lambda p: p.write_bytes(pdf)),
                            (kit.folder / f"{stem}.docx", lambda p: resume_render.write_docx(tailored, p)),
                            (kit.folder / f"{stem}.txt", lambda p: resume_render.write_text(tailored, p))):
            write(path)
            kit.files.append(path)

    # ---- 3. messages
    msgs, msg_problems = {}, {}
    if "messages" in parts:
        # Numbers a message may use: yours, and the job's title / company / id. Not the posting's body: it is
        # written by a stranger and could plant "10 years" or "500 million users" for the AI to claim as yours.
        allowed = (tailor.numbers(cvmod.as_text(base)) | tailor.numbers(rec["title"]) | tailor.numbers(rec["company"])
                   | tailor.numbers(outreach.job_ref(rec)) | tailor.numbers(str(profile.get("graduation_year") or "")))
        proof = [f["evidence"] for f in report["fit"] if f.get("id") and "not on the resume" not in f.get("evidence", "")]
        if not proof:
            first = (tailored.get("projects") or [{}])[0]
            proof = [b for b in (first.get("bullets") or [])[:2]]
        skills = report["keywords_have"][:6]
        if gemini:
            msgs, msg_problems = await _ai_messages(gemini, rec, profile, base, proof, skills, allowed, kit,
                                                    my_names)
        if not msgs:
            msgs = outreach.finish(outreach.templates(rec, profile, base, proof[0] if proof else "", skills), rec)
            msg_problems = outreach.check(msgs, allowed_numbers=allowed | tailor.numbers(rec.get("url", "")),
                                          ignore=[rec.get("title", ""), rec.get("company", ""), rec.get("url", "")])
            kit.notes.append("messages: templates (no AI)")
        msgs = {k: outreach.drop_unfilled(contact_private.restore(v)) for k, v in msgs.items()}
        for name, text in msgs.items():
            left = re.findall(r"\[(?!first name\])[^\]\n]{1,30}\]", text)
            if left:
                msg_problems.setdefault(name, []).append("fill in " + ", ".join(dict.fromkeys(left)))

    # ---- 4. prep
    if "prep" in parts:
        catalog = prep.load_catalog(settings.root)
        gaps = list(dict.fromkeys(report["keywords_missing"] or (rec.get("ai") or {}).get("gaps") or []))
        data = None
        if gemini and catalog:
            try:
                keys = list(catalog)
                topic_list = "\n".join(f"- {k}: {catalog[k].get('title', k)}" for k in keys)
                log("writing interview prep…")
                data, model = await gemini.generate_json(
                    prep.PREP_SYSTEM, f"{body_private.text}\n\nAllowed topic keys:\n{topic_list}\n\n{_job_block(rec)}",
                    prep.schema(keys), max_tokens=8192, temperature=0.4, patient=False)
                data = _restore(conform(data, prep.schema(keys)), body_private)
                ai_note("prep", model)
            except AuthError:
                raise
            except AIError as exc:
                kit.notes.append(f"prep: AI unavailable ({clip(str(exc), 120)}), made by keywords instead")
        if not data:
            data = prep.rule_prep(catalog, rec, base, cvmod.as_text(base))
            if not gemini:
                kit.notes.append("prep: topics by keyword, generic questions (no AI)")
        if len([t for t in data.get("topics") or [] if t.get("key") in catalog]) < 3:
            seen = {t.get("key") for t in data.get("topics") or []}
            data["topics"] = (data.get("topics") or []) + [t for t in prep.match_topics(catalog, job_text) if t["key"] not in seen]
        path = kit.folder / "prep.md"
        path.write_text(prep.render(data, catalog, rec, gaps), encoding="utf-8")
        kit.files.append(path)

    # ---- 5. kit.md
    path = kit.folder / "kit.md"
    path.write_text(render_kit(rec, report, msgs, msg_problems, stem, parts, humanize.resume_notes(
        [t for _, t in cvmod.lines_of(base)])), encoding="utf-8")
    kit.files.insert(0, path)
    for name, issues in msg_problems.items():
        kit.problems.append(f"{name.replace('_', ' ')}: {'; '.join(issues)}")
    return kit


async def _ai_messages(gemini, rec, profile, cv, proof, skills, allowed, kit, my_names) -> tuple[dict, dict]:
    college = ((cv.get("education") or [{}])[0].get("school") or "").strip()
    grad, current = outreach._who(profile, cv)
    ref = outreach.job_ref(rec)
    about = (f"CANDIDATE: {candidate(profile)}. College: {college or 'not given'}."
             + (f" Currently {current}." if current else "")
             + "\nPROOF (facts from the candidate's resume; use at most one or two per message):\n"
             + "\n".join(f"- {p}" for p in proof[:3])
             + (f"\nSKILLS THE JOB ASKS FOR THAT THE CANDIDATE HAS: {', '.join(skills)}" if skills else ""))
    about = redact(about, profile, header=False, names=my_names).text  # no contact details or name, ever
    prompt = (f"TODAY: {today().isoformat()} (if the class year has passed, the candidate has graduated: never call "
              f"them a student then)\n{about}\nJOB ID: {ref or 'none given'}\n\n{_job_block(rec, 3500)}")
    system = outreach.MSG_SYSTEM.format(banned=", ".join(
        w.strip() for w in humanize.MESSAGE_ALWAYS.replace("\n", "").split("|")[:45] if w.strip()))
    allowed = allowed | tailor.numbers(rec.get("url", ""))
    try:
        msgs, model = await gemini.generate_json(system, prompt, outreach.MSG_SCHEMA, max_tokens=4096,
                                                 temperature=0.7, patient=False)
    except AuthError:
        raise
    except AIError as exc:
        kit.notes.append(f"messages: AI unavailable ({clip(str(exc), 120)}), templates instead")
        return {}, {}
    msgs = conform(msgs, outreach.MSG_SCHEMA)
    msgs = {k: humanize.fix(msgs[k]) for k in outreach.ORDER}
    ignore = [rec.get("title", ""), rec.get("company", ""), rec.get("url", "")]
    mine = tailor.vocabulary(cv) | {w.lower() for w in re.findall(r"[A-Za-z][\w.+#-]*", cvmod.as_text(cv))}
    problems = outreach.check(msgs, allowed_numbers=allowed, ignore=ignore, resume_words=mine,
                              resume_text=cvmod.as_text(cv))
    if problems:  # one round of fixes: only messages that improve are kept
        try:
            fixed, _ = await gemini.generate_json(
                system, prompt + "\n\nYOUR DRAFT:\n" + json.dumps(msgs, ensure_ascii=False)
                + "\n\nRewrite only to fix these problems, keep everything else. Return all six messages.\n"
                + json.dumps(problems, ensure_ascii=False), outreach.MSG_SCHEMA, max_tokens=4096,
                temperature=0.5, patient=False)
            fixed = conform(fixed, outreach.MSG_SCHEMA)
            fixed = {k: humanize.fix(fixed[k]) for k in outreach.ORDER}
            again = outreach.check(fixed, allowed_numbers=allowed, ignore=ignore, resume_words=mine,
                              resume_text=cvmod.as_text(cv))
            for name in problems:
                if len(again.get(name, [])) < len(problems[name]) and fixed.get(name):
                    msgs[name] = fixed[name]
        except AIError:
            pass
    template = outreach.templates(rec, profile, cv, proof[0] if proof else "", skills)
    for name in ("referral_note", "alumni_note", "email_subject"):  # hard limits: a short template beats a cut-off note
        if len(msgs.get(name, "")) > outreach.LIMITS[name][1]:
            msgs[name] = template[name]
    still = outreach.check(msgs, allowed_numbers=allowed, ignore=ignore, resume_words=mine,
                           resume_text=cvmod.as_text(cv))
    for name, issues in still.items():  # a draft that still claims things you didn't write is never kept
        if any(i.startswith(("claims your resume", "numbers not from")) for i in issues):
            msgs[name] = template[name]
            kit.notes.append(f"{name.replace('_', ' ')}: the AI draft claimed things your resume doesn't show, "
                             "so a plain template is used instead")
    msgs = outreach.finish(msgs, rec)
    kit.notes.append(f"messages: {model}")
    return msgs, outreach.check(msgs, allowed_numbers=allowed, ignore=ignore, resume_words=mine,
                              resume_text=cvmod.as_text(cv))


TITLES = {
    "referral_note": "Referral request: LinkedIn connection note to someone at the company",
    "recruiter_dm": "Recruiter: LinkedIn message after you apply",
    "email_subject": "Cold email: subject",
    "email_body": "Cold email: body (attach the PDF)",
    "alumni_note": "Alumni: LinkedIn note to someone from your college (advice first, referral later)",
    "follow_up": "Follow-up: about a week after the referral request, only once",
}


def _fence(text: str) -> str:
    return "```text\n" + text.replace("```", "'''") + "\n```"


def render_kit(rec: dict, report: dict, msgs: dict, problems: dict, stem: str, parts, base_notes: list[str]) -> str:
    out = [f"# {md_text(rec['company'])}: {md_text(rec['title'])}", "",
           f"{safe_url(rec['url']) or 'not a web link'}  ",
           f"{md_text(rec.get('location') or '')} · posted {posted_label(rec)} · tracker #{rec['id']}", "",
           f"_Made {today().isoformat()}. Read every line before you upload or send anything: jobradar only knows "
           "your resume and the posting._", ""]
    out += ["## Files", ""]
    if "resume" in parts:
        out += [f"- `{stem}.pdf`: the tailored resume to upload", f"- `{stem}.docx`: the same resume to edit (Word, "
                "Google Docs)", f"- `{stem}.txt`: plain text for \"paste your resume\" boxes"]
    if "prep" in parts:
        out.append("- `prep.md`: interview prep with free resources")
    out.append("")
    if report.get("fit"):
        out += ["## Why you fit", ""]
        out += [f"- **{md_text(f.get('requirement', ''))}**: {md_text(f.get('evidence', ''))}" for f in report["fit"]]
        out.append("")
    if report.get("keywords_have") or report.get("keywords_missing"):
        out += ["## Keywords", ""]
        if report.get("keywords_have"):
            out.append("- On your resume: " + ", ".join(md_text(k) for k in report["keywords_have"]))
        if report.get("keywords_missing"):
            out.append("- Asked for but not on your resume (gaps: not added, see prep.md): "
                       + ", ".join(md_text(k) for k in report["keywords_missing"]))
        out.append("")
    if "resume" in parts:
        out += ["## What changed in your resume", ""]
        if report["rewrites"]:
            out.append(f"Reworded ({len(report['rewrites'])}), using the job's terms for things you did:")
            for r in report["rewrites"]:
                out += [f"- before: {md_text(r['before'])}", f"  - after: {md_text(r['after'])}"
                        + (f" _({md_text(r['reason'])})_" if r.get("reason") else "")]
        else:
            out.append("- No bullet was reworded: your own wording already fits.")
        out += [f"- {md_text(m)}" for m in report["moved"]]
        out += [f"- trimmed to fit the page: {md_text(t)}" for t in report.get("trimmed") or []]
        if report["refused"]:
            out += ["", "Suggested rewordings that failed jobradar's checks (your original was kept):"]
            out += [f"- {md_text(r['proposed'])}  \n  _{md_text(r['why'])}_" for r in report["refused"]]
        out.append("")
    if base_notes:
        out += ["## Tips on your base resume", "", f"Worth fixing once in profile/{cvmod.CV_FILE} (jobradar doesn't "
                "rewrite these for you):", ""] + [f"- {n}" for n in base_notes] + [""]
    if msgs:
        out += ["## Messages", ""]
        for name in outreach.ORDER:
            text = msgs.get(name, "")
            size, unit = outreach._measure(name, text)
            out += [f"### {TITLES[name]}", f"_{size}/{outreach.LIMITS[name][1]} {unit}_", "", _fence(text)]
            if problems.get(name):
                out.append("> check: " + "; ".join(md_text(p) for p in problems[name]))
            out.append("")
        out += ["## How to use these", "", outreach.HOW_TO_USE]
    return "\n".join(out).rstrip() + "\n"
