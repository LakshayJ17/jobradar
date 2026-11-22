"""One run: fetch every board -> rule filter -> fetch details for new candidates -> store -> AI -> report -> notify."""

from __future__ import annotations

import asyncio
import os
from collections import Counter

from . import notify, sources
from .ai import AuthError, Gemini, score_jobs
from .config import Settings, resume_text
from .filters import Rules
from .http import Http
from .models import Job
from .privacy import redact
from .salary import in_description
from .report import render_markdown, write_reports
from .store import Store
from .util import today


def refilter(store: Store, rules: Rules) -> tuple[int, int]:
    """Apply the current profile.yaml to tracked jobs, every run, so editing filters (salary floor, cities, titles)
    takes effect without re-downloading anything. Only touches jobs you haven't acted on: a 'new' job that no
    longer fits becomes 'skipped' (auto), and an auto-skipped job that fits again comes back. Returns
    (hidden, restored). Age is not re-checked: a tracked job stays until its board takes it down."""
    fields = Job.__dataclass_fields__
    hidden = restored = 0
    for rec in store.all():
        if rec.get("closed"):
            continue
        values = {k: rec.get(k) for k in fields if k in rec and k not in ("detail", "needs_detail")}
        values["native_id"] = rec["key"].split(":", 2)[-1]
        job = Job(**values)
        job.description = rec.get("description") or ""
        if not rec.get("salary") and job.description:
            rec["salary"] = job.salary = in_description(job.description, rules.currency)
        rec["rank_bonus"] = rules.rank_bonus(job)
        rec["rule_score"], rec["signals"], rec["min_years"] = rules.score(job)  # skills / cities may have changed
        status, note = rec.get("status"), rec.get("notes") or ""
        auto = rec.get("auto_skip") or (status == "skipped" and note.startswith("auto:") and "\n" not in note)
        if status != "new" and not (status == "skipped" and auto):
            continue  # your own statuses are never changed
        reason = rules.prefilter(job, ignore_age=True) or (
            rules.postfilter(job, ignore_age=True) if job.description else None)
        if reason and status == "new":
            store.set_status(rec["id"], "skipped", note=f"auto: {reason}")
            rec["auto_skip"] = reason
            hidden += 1
        elif not reason and status == "skipped":
            store.set_status(rec["id"], "new")
            rec.pop("auto_skip", None)
            rec["notes"] = "\n".join(x for x in note.splitlines() if not x.startswith("auto:"))
            restored += 1
        elif reason:
            rec["auto_skip"] = reason
    return hidden, restored


def fill_salary(job: Job, currency: str = "INR") -> None:
    """Boards without a pay field often state it in the text ("CTC: 6-8 LPA"). Amounts with no currency are read
    as yours (profile salary.currency)."""
    if not job.salary and job.description:
        job.salary = in_description(job.description, currency)


def gemini_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


async def collect(settings: Settings, store: Store, *, only: list[str] | None = None, log=print) -> dict:
    rules = Rules(settings.profile)
    targets = sources.localize(sources.build_targets(settings.sources, only),
                               (settings.profile.get("locations") or {}).get("country") or "")
    stats: dict = {"date": today().isoformat(), "targets": len(targets), "fetched": 0, "candidates": 0,
                   "new": 0, "dropped": Counter(), "errors": {}, "detail_errors": {}, "closed": 0,
                   "by_source": Counter(), "deferred": 0}

    async with Http() as http:
        async def fetch_one(t):
            try:
                return t, await sources.fetch(http, t), None
            except Exception as exc:  # one broken board must never stop the run
                return t, [], f"{type(exc).__name__}: {exc}"[:220]

        results = await asyncio.gather(*(fetch_one(t) for t in targets))
        ok_boards: set[str] = set()
        postings = {}
        for target, jobs, err in results:
            if err:
                stats["errors"][target.label] = err
                continue
            if sources.complete_listing(target.source):
                ok_boards.add(target.label)  # only full listings can tell us a job was taken down
            stats["by_source"][target.source] += len(jobs)
            for job in jobs:
                postings.setdefault(job.key, job)
        stats["fetched"] = len(postings)

        fresh = []
        for key, job in postings.items():
            if store.has(key):
                store.touch(key)
                rec = store.jobs.get(key)
                if rec is not None and job.salary and not rec.get("salary"):
                    rec["salary"] = job.salary  # boards that started listing pay, or records from before pay was read
                continue
            fill_salary(job, rules.currency)
            reason = rules.prefilter(job)
            if reason:
                stats["dropped"][reason] += 1
            else:
                fresh.append(job)
        stats["candidates"] = len(fresh)

        async def enrich_one(job):
            try:
                await sources.enrich(http, job)
                return None
            except Exception as exc:
                return f"{type(exc).__name__}: {exc}"[:160]

        need, per_board = [], Counter()
        for job in [j for j in fresh if j.needs_detail]:
            cap = job.detail.get("cap")
            per_board[f"{job.source}:{job.board}"] += 1
            if cap and per_board[f"{job.source}:{job.board}"] > cap:
                stats["deferred"] += 1  # politeness cap: picked up on the next run
                continue
            need.append(job)
        deferred_keys = {j.key for j in fresh if j.needs_detail} - {j.key for j in need}
        fresh = [j for j in fresh if j.key not in deferred_keys]
        if need:
            log(f"fetching details for {len(need)} new candidates…")
        failures = dict(zip((j.key for j in need), await asyncio.gather(*(enrich_one(j) for j in need))))
        detail_errors: dict[str, list] = {}
        for job in fresh:
            if failures.get(job.key):
                entry = detail_errors.setdefault(job.source, [0, failures[job.key]])
                entry[0] += 1  # not stored, so retried next run
                continue
            fill_salary(job, rules.currency)
            reason = rules.postfilter(job)
            if reason:
                store.reject(job.key, reason)
                stats["dropped"][reason.split("-")[0] if reason.startswith("batch") else reason] += 1
                continue
            score, signals, years = rules.score(job)
            store.add(job, rule_score=score, signals=signals, min_years=years)["rank_bonus"] = rules.rank_bonus(job)
            stats["new"] += 1
        stats["detail_errors"] = {k: {"count": v[0], "sample": v[1]} for k, v in detail_errors.items()}
        stats["closed"] = store.close_missing(ok_boards, set(postings)) + store.close_expired()
        stats["requests"] = http.requests
    return stats


async def run(settings: Settings, *, use_ai: bool = True, send: bool = True, only: list[str] | None = None,
              recheck: bool = False, rescore: bool = False, log=print) -> dict:
    store = Store(settings.store_path)
    if recheck:
        store.rejected.clear()  # postings rejected earlier get evaluated again (their pages are re-read)
    if rescore:
        for rec in store.all():
            if not rec.get("closed") and rec.get("description"):
                rec["ai"] = None
    stats = await collect(settings, store, only=only, log=log)
    hidden, restored = refilter(store, Rules(settings.profile))  # after collect: uses pay found this run too
    if hidden or restored:
        log(f"filters: {hidden} tracked jobs no longer fit profile.yaml (skipped), {restored} fit again (restored)")

    ai_cfg = settings.profile.get("ai") or {}
    stats["ai"] = "off"
    if use_ai and ai_cfg.get("enabled", True):
        key = gemini_key()
        if not key:
            stats["ai"] = "skipped (no GEMINI_API_KEY)"
        else:
            resume = redact(resume_text(settings)[0], settings.profile).text  # no contact details to Gemini
            gemini = Gemini(key, ai_cfg.get("models") or [], rpm=int(ai_cfg.get("rpm", 10)))
            try:
                pending = sum(1 for r in store.all() if r.get("ai") is None and not r.get("closed")
                              and r.get("status") in ("new", "saved"))
                if pending:
                    log(f"AI scoring up to {min(pending, int(ai_cfg.get('max_jobs_per_run', 80)))} jobs "
                        f"(free tier, ~{60 // max(1, int(ai_cfg.get('rpm', 10)))}s between requests)…")
                stats["ai"] = await score_jobs(store, settings.profile, gemini, resume, log=log)
                stats["ai"]["calls"] = gemini.calls
            except AuthError as exc:
                stats["ai"] = f"failed: {exc}"
            finally:
                await gemini.aclose()

    stats["dropped"] = dict(stats["dropped"].most_common())
    stats["by_source"] = dict(stats["by_source"].most_common())
    store.record_run({k: v for k, v in stats.items() if k in ("date", "targets", "fetched", "candidates", "new",
                                                              "closed", "requests")} | {"errors": len(stats["errors"])})
    stats["notify"] = notify.deliver(store, settings.profile.get("notify") or {}, stats["new"]) if send else "off"
    store.save()
    stats["reports"] = write_reports(store, stats, settings.reports_dir, settings.data_dir / "jobs.csv")
    notify.github_summary(render_markdown(store, stats))
    return stats
