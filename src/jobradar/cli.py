"""jobradar command line."""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
import webbrowser

from . import pipeline
from .ai import AIError, AuthError, Gemini
from .config import init_files, load_settings, resume_text
from .filters import Rules, expand_cities
from .salary import RATES
from .util import COUNTRIES, country_name, country_names
from .http import Http
from .privacy import extraction_warnings, redact
from .report import posted_label, reviews_url, why
from .salary import label as pay_label
from .salary import short as pay_short
from .salary import unit as pay_unit
from .sources import MODULES, SLUG_SOURCES, Target, build_targets
from .store import STATUSES, Store
from .util import clean_block, clean_line, clip, today


def _print_table(rows: list[dict], store: Store) -> None:
    width = shutil.get_terminal_size((140, 20)).columns
    title_w = max(24, min(60, width - 103))
    print(f"{'ID':>5}  {'SCORE':>5}  {'AI':<6} {'COMPANY':<18} {'ROLE':<{title_w}} {'LOCATION':<22} {f'PAY ({pay_unit()})':<11} "
          f"{'POSTED':<8} STATUS")
    for r in rows:
        ai = r.get("ai") or {}
        verdict = "no" if ai.get("eligible") == "no" else ai.get("verdict", "-")
        print(f"{r['id']:>5}  {store.score_of(r):>5}  {clean_line(verdict):<6} {clip(clean_line(r['company']), 18):<18} "
              f"{clip(clean_line(r['title']), title_w):<{title_w}} "
              f"{clip(('* ' if r.get('rank_bonus') else '') + clean_line(r.get('location')), 22):<22} "
              f"{pay_short(r.get('salary')) or '-':<11} "
              f"{posted_label(r):<8} {clean_line(r.get('status'))}")


def cmd_run(args, settings) -> int:
    stats = asyncio.run(pipeline.run(settings, use_ai=not args.no_ai, send=not args.no_notify,
                                     only=args.only, recheck=args.recheck, rescore=args.rescore))
    dropped = ", ".join(f"{k} {v}" for k, v in list(stats["dropped"].items())[:8])
    print(f"\njobradar · {stats['date']}")
    print(f"  boards     {stats['targets']} checked, {len(stats['errors'])} failed · {stats.get('requests', 0)} requests")
    print(f"  postings   {stats['fetched']} listed → {stats['candidates']} unseen candidates → {stats['new']} new matches")
    if stats.get("by_source"):
        print("  platforms  " + ", ".join(f"{k} {v}" for k, v in stats["by_source"].items()))
    if stats.get("deferred"):
        print(f"  deferred   {stats['deferred']} job pages left for the next run (per-site politeness cap)")
    if dropped:
        print(f"  dropped    {dropped}")
    for source, info in stats["detail_errors"].items():
        print(f"  retry      {info['count']} {source} descriptions failed, retried next run "
              f"({clip(clean_line(info['sample']), 80)})")
    ai = stats["ai"]
    if isinstance(ai, dict):
        print(f"  AI         {ai['scored']}/{ai['pending']} scored with {', '.join(ai['models']) or '-'} "
              f"({ai['ineligible']} marked ineligible, {ai.get('calls', 0)} requests)"
              + (f" · {len(ai['errors'])} batch errors: {clip(ai['errors'][-1], 120)}" if ai["errors"] else ""))
    else:
        print(f"  AI         {ai}")
    if stats["closed"]:
        print(f"  closed     {stats['closed']} tracked postings were taken down")
    if isinstance(stats["notify"], dict) and stats["notify"].get("items"):
        sent = ", ".join(f"{k}: {v}" for k, v in stats["notify"].items() if k != "items")
        print(f"  digest     {stats['notify']['items']} jobs · {sent}")
    for label, err in sorted(stats["errors"].items()):
        print(f"  ! {clean_line(label)}: {clip(clean_line(err), 110)}")
    print(f"  report     {stats['reports'].get('html', 'not written (see errors above)')}")
    store = Store(settings.store_path)
    new = store.ranked([r for r in store.all() if store.visible(r) and r.get("first_seen") == today().isoformat()])
    if new:
        print(f"\nTop new matches ({len(new)} total):")
        _print_table(new[:15], store)
    return 0


def cmd_list(args, settings) -> int:
    store = Store(settings.store_path)
    rows = store.all() if args.all else [r for r in store.all() if store.visible(r)]
    if args.status:
        rows = [r for r in store.all() if r.get("status") == args.status]
    if args.new:
        rows = [r for r in rows if r.get("first_seen") == today().isoformat()]
    if args.company:
        rows = [r for r in rows if args.company.lower() in r["company"].lower()]
    if args.min:
        rows = [r for r in rows if store.score_of(r) >= args.min]
    rows = store.ranked(rows)
    if not rows:
        print("Nothing here yet. Run `jobradar run` first." if not store.jobs else "No jobs match those filters.")
        return 0
    _print_table(rows[: args.limit], store)
    if len(rows) > args.limit:
        print(f"… {len(rows) - args.limit} more (use --limit)")
    if any(r.get("rank_bonus") for r in rows[: args.limit]):
        print("* preferred location (profile.yaml locations.preferred, or remote): ranked higher, score unchanged")
    return 0


def cmd_show(args, settings) -> int:
    store = Store(settings.store_path)
    rec = store.get(args.id)
    if not rec:
        print(f"No job #{args.id}")
        return 1
    ai = rec.get("ai") or {}
    line = clean_line  # job text is from strangers: never print control characters or escape sequences
    print(f"#{rec['id']}  {line(rec['company'])} - {line(rec['title'])}")
    print(f"  {line(rec['url'])}")
    print(f"  location {line(rec.get('location'))} · posted {rec.get('posted_at') or '?'} · source {line(rec['source'])} · "
          f"status {line(rec.get('status'))}{' · CLOSED' if rec.get('closed') else ''}")
    print(f"  pay {pay_label(rec.get('salary')) or 'not listed'} · "
          f"{'preferred location' if rec.get('rank_bonus') else 'not a preferred location'}")
    print(f"  reviews {reviews_url(rec['company'])}")
    print(f"  rule score {rec.get('rule_score')} · {line(', '.join(rec.get('signals') or [])) or '-'}")
    if ai:
        print(f"  AI fit {ai.get('fit')} · {line(ai.get('verdict'))} · eligible {line(ai.get('eligible'))} · "
              f"min years {ai.get('min_years')} · {line(ai.get('model'))}")
        print(f"  why: {line(ai.get('why'))}")
        if ai.get("gaps"):
            print(f"  gaps: {line(', '.join(str(g) for g in ai['gaps']))}")
    if rec.get("notes"):
        print(f"  notes: {line(rec['notes'])}")
    if rec.get("description"):
        print("\n" + clip(clean_block(rec["description"]), args.chars))
    return 0


def cmd_mark(args, settings) -> int:
    *ids, status = args.items
    if status not in STATUSES or not ids:
        print(f"usage: jobradar mark <id> [<id> ...] <{'|'.join(STATUSES)}>")
        return 2
    store = Store(settings.store_path)
    for job_id in ids:
        try:
            rec = store.set_status(int(job_id), status, args.note)
            print(f"#{rec['id']} {clean_line(rec['company'])} - {clean_line(rec['title'])} → {status}")
        except (KeyError, ValueError) as exc:
            print(f"#{job_id}: {exc}")
    store.save()
    return 0


def cmd_draft(args, settings) -> int:
    from . import kit

    store = Store(settings.store_path)
    rec = store.get(args.id)
    if not rec:
        print(f"No job #{args.id}")
        return 1
    key = None if args.no_ai else pipeline.gemini_key()
    parts = tuple(args.only or ("resume", "messages", "prep"))
    print(f"Application kit for #{rec['id']} {clean_line(rec['company'])} - {clean_line(rec['title'])}"
          + ("" if key else " (no GEMINI_API_KEY: rules and templates only)"))
    try:
        result = asyncio.run(kit.build(settings, rec, key, parts=parts, log=lambda m: print(f"  {m}")))
    except (kit.KitError, AuthError) as exc:
        print(f"Can't make the kit: {exc}")
        return 1
    for note in result.notes:
        print(f"  {clean_line(note)}")
    print(f"\nSaved in {result.folder}")
    for path in result.files:
        print(f"  {path.name}")
    if result.problems:
        print("\nCheck before sending:")
        for p in result.problems:
            print(f"  ! {clean_line(p)}")
    if rec.get("status") == "new":
        store.set_status(rec["id"], "saved")
        store.save()
    return 0


def cmd_probe(args, settings) -> int:
    rules = Rules(settings.profile)

    async def go():
        found = []
        async with Http() as http:
            for slug in args.slugs:
                for source in SLUG_SOURCES:
                    t = Target(source, slug, slug)
                    try:
                        jobs = await MODULES[source].fetch(http, t)
                    except Exception:
                        continue
                    if not jobs:
                        continue
                    local = sum(1 for j in jobs if rules.location_ok(j))
                    company = jobs[0].company if source != "smartrecruiters" else slug
                    print(f"  {slug:<20} {source:<16} {len(jobs):>5} jobs · {local:>4} in your locations")
                    found.append((source, slug, company))
        return found

    print("Probing public job boards…")
    found = asyncio.run(go())
    if not found:
        print("Not found on Greenhouse / Lever / Ashby / SmartRecruiters / Workable under that slug.\n"
              "Tip: open the company's careers page - the job links show the board and slug "
              "(boards.greenhouse.io/<slug>, jobs.lever.co/<slug>, jobs.ashbyhq.com/<slug>, "
              "*.myworkdayjobs.com/<site>).")
        return 1
    print("\nAdd to config/sources.yaml:")
    for source, slug, _ in found:
        print(f"  {source}:\n    {slug}: {slug.capitalize()}")
    return 0


def profile_warnings(p: dict) -> list[str]:
    """Settings that contradict each other, or that jobradar can't use."""
    out = []
    locs = p.get("locations") or {}
    country = country_name(locs.get("country") or "")
    if locs.get("country") and country not in COUNTRIES:
        out.append(f"locations.country {locs['country']!r} isn't one jobradar knows: career sites that filter by "
                   "country search everywhere instead (the location filter still applies)")
    accept = expand_cities(locs.get("accept"), locs.get("groups"))
    if country and accept and not set(country_names(country)) & set(accept):
        out.append(f"locations.accept doesn't mention {country}: jobs whose location only names the country "
                   f"are dropped (add {country.lower()!r})")
    cur = str((p.get("salary") or {}).get("currency") or "INR").upper()
    if cur not in RATES:
        out.append(f"salary.currency {cur} is unknown, so pay is shown in INR. Known: {', '.join(RATES)}")
    if int(p.get("min_years_required") or 0) > int(p.get("max_years_required", 1)):
        out.append("min_years_required is above max_years_required: every job that states its years is dropped")
    exp = int(p.get("experience_years") or 0)
    if exp >= 3 and "senior" in (p.get("exclude_levels") or []):
        out.append(f"experience_years is {exp} but exclude_levels drops 'senior' jobs: use [director, executive]")
    return out


def cmd_init(args, settings) -> int:
    print(f"Setting up {settings.root}")
    for path, action in init_files(settings.root):
        print(f"  {path:<20} {action}")
    print("\nNext:\n"
          "  1. edit config/profile.yaml: graduation_year, skills, locations.preferred, salary.min_lpa\n"
          "  2. copy your resume into profile/ (any name; .pdf, .txt or .md). It never leaves this machine\n"
          "     except as redacted text for the AI (see `jobradar resume`)\n"
          "  3. optional: put a free GEMINI_API_KEY in .env (https://aistudio.google.com/apikey)\n"
          "  4. uv run jobradar doctor, then uv run jobradar run")
    return 0


def cmd_doctor(args, settings) -> int:
    print(f"project   {settings.root}")
    print(f"profile   {settings.profile_from}")
    if not settings.own_profile:
        print("          ! this is the template: run `jobradar init`, then edit config/profile.yaml")
    if not settings.profile.get("skills"):
        print("          ! skills is empty: list yours in config/profile.yaml (they raise the rule score)")
    for w in profile_warnings(settings.profile):
        print(f"          ! {w}")
    targets = build_targets(settings.sources)
    counts = {}
    for t in targets:
        counts[t.source] = counts.get(t.source, 0) + 1
    print(f"sources   {len(targets)} boards · " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    resume, source = resume_text(settings)
    print(f"resume    {source} ({len(resume)} chars)")
    if not source.startswith("profile.yaml"):
        for w in extraction_warnings(resume):
            print(f"          ! {w}")
        print(f"          sent to Gemini without: {'; '.join(redact(resume, settings.profile).removed) or 'nothing found'}"
              " (see `jobradar resume`)")
    store = Store(settings.store_path)
    print(f"tracker   {len(store.jobs)} jobs tracked · last run {store.runs[-1]['date'] if store.runs else 'never'}")
    import os

    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_TO"):
        print(f"{'env':<9} {name}: {'set' if os.environ.get(name) else 'not set'}")
    key = pipeline.gemini_key()
    if not key:
        print("gemini    GEMINI_API_KEY not set - AI scoring and drafts are off (rule-based mode still works)")
        return 0
    ai_cfg = settings.profile.get("ai") or {}

    async def ping():
        import httpx

        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://generativelanguage.googleapis.com/v1beta/models",
                                 params={"pageSize": 300}, headers={"x-goog-api-key": key})
        if r.status_code != 200:
            return f"key check failed (HTTP {r.status_code})", []
        names = {m["name"].removeprefix("models/") for m in r.json().get("models", [])}
        wanted = list(dict.fromkeys((ai_cfg.get("models") or []) + (ai_cfg.get("draft_models") or [])))
        status = [(m, m in names) for m in wanted]
        gemini = Gemini(key, [m for m, ok in status if ok][:1], rpm=60)
        try:
            text, model = await gemini.generate("", "Reply with the single word OK.", max_tokens=256)
            return f"key works · {model} replied {text.strip()[:20]!r}", status
        except AIError as exc:
            return f"key valid but generation failed: {exc}", status
        finally:
            await gemini.aclose()

    msg, status = asyncio.run(ping())
    print(f"gemini    {msg}")
    for model, ok in status:
        print(f"          {model}: {'available' if ok else 'NOT available on this key - edit ai.models'}")
    return 0


def cmd_resume(args, settings) -> int:
    from . import cv as cvmod
    from . import humanize, kit, resume_render

    if args.setup:
        path = kit.cv_path(settings)
        if path.exists() and not args.force:
            print(f"{path.relative_to(settings.root)} already exists (edit it, or add --force to rebuild it from your "
                  "resume file)")
            return 1
        key = pipeline.gemini_key()
        if not key:
            print("--setup needs a free GEMINI_API_KEY in .env. Without one, copy config/resume.example.yaml to "
                  f"profile/{cvmod.CV_FILE} and fill it in")
            return 1

        async def go():
            gemini = kit._gemini(settings, key)
            try:
                return await kit.setup_cv(settings, gemini)
            finally:
                await gemini.aclose()

        try:
            cv, warnings = asyncio.run(go())
        except (AIError, kit.KitError) as exc:
            print(f"Setup failed: {exc}")
            return 1
        print(f"Wrote {path.relative_to(settings.root)}. Check it once with `jobradar resume --pdf`.")
        for w in warnings:
            print(f"  ! {w}")
        return 0
    if args.pdf or args.check:
        cv = cvmod.load(kit.cv_path(settings))
        if cv is None:
            print(f"No profile/{cvmod.CV_FILE} yet: run `jobradar resume --setup` first")
            return 1
        if args.check:
            bullets = [t for _, t in cvmod.lines_of(cv)]
            notes = humanize.resume_notes(bullets)
            print(f"{len(bullets)} bullets checked")
            for n in notes or ["nothing to flag"]:
                print(f"  - {n}")
        if args.pdf:
            out = settings.data_dir / "resume"
            out.mkdir(parents=True, exist_ok=True)
            stem = resume_render.file_stem(cv)
            pdf = resume_render.pdf_bytes(cv, (settings.profile.get("resume") or {}).get("style") or {})
            (out / f"{stem}.pdf").write_bytes(pdf)
            resume_render.write_docx(cv, out / f"{stem}.docx")
            print(f"Your base resume as jobradar renders it ({resume_render.page_count(pdf)} page(s)): "
                  f"{out / (stem + '.pdf')}  (and .docx)")
        return 0
    resume, source = resume_text(settings)
    private = redact(resume, settings.profile)
    note = lambda line: print(line, file=sys.stderr)  # notes on stderr, so stdout can be piped into a secret
    note(f"# Resume as Gemini sees it  (from {source}, {len(private.text)} of {len(resume)} chars)")
    for line in private.removed:
        note(f"# removed: {line}")
    for w in extraction_warnings(resume) if not source.startswith("profile.yaml") else []:
        note(f"# ! {w}")
    note("")
    print(private.text)
    return 0


def cmd_export(args, settings) -> int:
    from pathlib import Path

    from .export import summary_line, write_xlsx

    store = Store(settings.store_path)
    if not store.jobs:
        print("Nothing tracked yet. Run `jobradar run` first.")
        return 1
    path = write_xlsx(store, Path(args.out).expanduser() if args.out else settings.data_dir / "jobs.xlsx")
    print(f"{path}  ({summary_line(store)})")
    return 0


def cmd_mail(args, settings) -> int:
    import os

    from . import notify
    from .export import summary_line, write_xlsx

    store = Store(settings.store_path)
    if not store.jobs:
        print("Nothing tracked yet. Run `jobradar run` first.")
        return 1
    to = args.to or os.environ.get("EMAIL_TO") or os.environ.get("SMTP_USER")
    if to and not re.fullmatch(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}", to):
        print(f"{to!r} doesn't look like one email address")
        return 2
    path = write_xlsx(store, settings.data_dir / "jobs.xlsx")
    print(f"Spreadsheet: {path} ({summary_line(store)})")
    result = notify.mail_spreadsheet(store, path, to=to)
    if result == "sent":
        print(f"Emailed to {to}")
        return 0
    print(f"Email {result}")
    if "not set" in result:
        print("Set SMTP_USER (your Gmail address) and SMTP_PASSWORD (a Google App password) in .env, and EMAIL_TO "
              "(or pass --to). Other providers: also set SMTP_HOST and SMTP_PORT (587 for STARTTLS).")
    return 1


def cmd_open(args, settings) -> int:
    path = settings.reports_dir / "latest.html"
    if not path.exists():
        print("No report yet - run `jobradar run` first.")
        return 1
    webbrowser.open(path.as_uri())
    print(path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobradar", description="Find jobs that fit your profile, every day.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run", help="fetch, filter, score, report, notify")
    p.add_argument("--no-ai", action="store_true", help="rule-based only")
    p.add_argument("--no-notify", action="store_true", help="don't send Telegram / email")
    p.add_argument("--only", nargs="+", help="limit to sources/companies, e.g. --only greenhouse nvidia")
    p.add_argument("--recheck", action="store_true", help="re-read postings rejected earlier (filter edits already apply to tracked jobs every run)")
    p.add_argument("--rescore", action="store_true", help="AI re-scores open jobs (after updating your resume / skills)")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("list", help="show tracked matches")
    p.add_argument("--all", action="store_true", help="include closed / ineligible / skipped")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--new", action="store_true", help="only jobs first seen today")
    p.add_argument("--company")
    p.add_argument("--min", type=int, help="minimum score")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("show", help="details for one job")
    p.add_argument("id", type=int)
    p.add_argument("--chars", type=int, default=2500)
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("mark", help=f"update status: {', '.join(STATUSES)}")
    p.add_argument("items", nargs="+", metavar="ID... STATUS")
    p.add_argument("--note")
    p.set_defaults(fn=cmd_mark)

    p = sub.add_parser("draft", help="application kit for one job: tailored resume (PDF/Word), messages, interview prep")
    p.add_argument("id", type=int)
    p.add_argument("--no-ai", action="store_true", help="rules and templates only")
    p.add_argument("--only", nargs="+", choices=["resume", "messages", "prep"], help="make only these parts")
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("probe", help="find which public job board a company uses")
    p.add_argument("slugs", nargs="+")
    p.set_defaults(fn=cmd_probe)

    sub.add_parser("init", help="create your config/profile.yaml and .env from the templates") \
        .set_defaults(fn=cmd_init)
    sub.add_parser("doctor", help="check config, resume, keys").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("resume", help="print your resume as the AI sees it; --setup / --pdf / --check")
    p.add_argument("--setup", action="store_true", help="build profile/resume.yaml from your resume file (once)")
    p.add_argument("--force", action="store_true", help="with --setup: rebuild an existing profile/resume.yaml")
    p.add_argument("--pdf", action="store_true", help="render profile/resume.yaml to data/resume/ to check it")
    p.add_argument("--check", action="store_true", help="flag wording recruiters read as AI-written or filler")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("export", help="write every tracked job to a spreadsheet (data/jobs.xlsx)")
    p.add_argument("--out", help="where to save it (default data/jobs.xlsx)")
    p.set_defaults(fn=cmd_export)

    p = sub.add_parser("mail", help="email the jobs spreadsheet to you (EMAIL_TO, or --to)")
    p.add_argument("--to", help="email address to send it to")
    p.set_defaults(fn=cmd_mail)
    sub.add_parser("open", help="open the HTML report").set_defaults(fn=cmd_open)

    args = parser.parse_args(argv)
    settings = load_settings()
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
