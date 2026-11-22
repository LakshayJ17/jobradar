"""jobradar command line."""

from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
import webbrowser

from . import pipeline
from .ai import AIError, Gemini, draft
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
from .util import clip, today


def _print_table(rows: list[dict], store: Store) -> None:
    width = shutil.get_terminal_size((140, 20)).columns
    title_w = max(24, min(60, width - 103))
    print(f"{'ID':>5}  {'SCORE':>5}  {'AI':<6} {'COMPANY':<18} {'ROLE':<{title_w}} {'LOCATION':<22} {f'PAY ({pay_unit()})':<11} "
          f"{'POSTED':<8} STATUS")
    for r in rows:
        ai = r.get("ai") or {}
        verdict = "no" if ai.get("eligible") == "no" else ai.get("verdict", "-")
        print(f"{r['id']:>5}  {store.score_of(r):>5}  {verdict:<6} {clip(r['company'], 18):<18} "
              f"{clip(r['title'], title_w):<{title_w}} {clip(('* ' if r.get('rank_bonus') else '') + (r.get('location') or ''), 22):<22} "
              f"{pay_short(r.get('salary')) or '-':<11} "
              f"{posted_label(r):<8} {r.get('status')}")


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
        print(f"  retry      {info['count']} {source} descriptions failed, retried next run ({clip(info['sample'], 80)})")
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
        print(f"  ! {label}: {clip(err, 110)}")
    print(f"  report     {stats['reports']['html']}")
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
    print(f"#{rec['id']}  {rec['company']} - {rec['title']}")
    print(f"  {rec['url']}")
    print(f"  location {rec.get('location')} · posted {rec.get('posted_at') or '?'} · source {rec['source']} · "
          f"status {rec.get('status')}{' · CLOSED' if rec.get('closed') else ''}")
    print(f"  pay {pay_label(rec.get('salary')) or 'not listed'} · "
          f"{'preferred location' if rec.get('rank_bonus') else 'not a preferred location'}")
    print(f"  reviews {reviews_url(rec['company'])}")
    print(f"  rule score {rec.get('rule_score')} · {', '.join(rec.get('signals') or []) or '-'}")
    if ai:
        print(f"  AI fit {ai.get('fit')} · {ai.get('verdict')} · eligible {ai.get('eligible')} · "
              f"min years {ai.get('min_years')} · {ai.get('model')}")
        print(f"  why: {ai.get('why')}")
        if ai.get("gaps"):
            print(f"  gaps: {', '.join(ai['gaps'])}")
    if rec.get("notes"):
        print(f"  notes: {rec['notes']}")
    if rec.get("description"):
        print("\n" + clip(rec["description"], args.chars))
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
            print(f"#{rec['id']} {rec['company']} - {rec['title']} → {status}")
        except (KeyError, ValueError) as exc:
            print(f"#{job_id}: {exc}")
    store.save()
    return 0


def cmd_draft(args, settings) -> int:
    key = pipeline.gemini_key()
    if not key:
        print("Set GEMINI_API_KEY in .env first (free key: https://aistudio.google.com/apikey)")
        return 1
    store = Store(settings.store_path)
    rec = store.get(args.id)
    if not rec:
        print(f"No job #{args.id}")
        return 1
    resume, source = resume_text(settings)
    private = redact(resume, settings.profile)  # Gemini sees placeholders; real values go back in below
    print(f"Drafting for #{rec['id']} {rec['company']} - {rec['title']} (resume: {source})…")
    ai_cfg = settings.profile.get("ai") or {}

    async def go():
        gemini = Gemini(key, ai_cfg.get("models") or [], rpm=int(ai_cfg.get("rpm", 10)))
        try:
            return await draft(rec, private.text, settings.profile, gemini)
        finally:
            await gemini.aclose()

    try:
        text, model = asyncio.run(go())
    except AIError as exc:
        print(f"Draft failed: {exc}")
        return 1
    text = private.restore(text)  # local only: drafts/ is git-ignored
    slug = re.sub(r"[^a-z0-9]+", "-", f"{rec['company']}-{rec['title']}".lower()).strip("-")[:60]
    path = settings.drafts_dir / f"{rec['id']}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# {rec['company']} - {rec['title']}\n\n{rec['url']}\n\n"
              f"_Drafted {today().isoformat()} with {model}. Check every line before sending - it only knows your resume._\n\n")
    path.write_text(header + text.strip() + "\n", encoding="utf-8")
    print(f"\n{text.strip()}\n\nSaved to {path}")
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

    p = sub.add_parser("draft", help="AI application kit for one job (fit, resume tweaks, referral DM, email, prep)")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_draft)

    p = sub.add_parser("probe", help="find which public job board a company uses")
    p.add_argument("slugs", nargs="+")
    p.set_defaults(fn=cmd_probe)

    sub.add_parser("init", help="create your config/profile.yaml and .env from the templates") \
        .set_defaults(fn=cmd_init)
    sub.add_parser("doctor", help="check config, resume, keys").set_defaults(fn=cmd_doctor)
    sub.add_parser("resume", help="print your resume exactly as the AI sees it (personal details removed)") \
        .set_defaults(fn=cmd_resume)
    sub.add_parser("open", help="open the HTML report").set_defaults(fn=cmd_open)

    args = parser.parse_args(argv)
    settings = load_settings()
    return args.fn(args, settings)


if __name__ == "__main__":
    sys.exit(main())
