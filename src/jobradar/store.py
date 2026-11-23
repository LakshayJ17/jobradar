"""Tracker state in one JSON file (data/jobs.json): git-friendly so GitHub Actions can commit it daily."""

from __future__ import annotations

import json
import os
from datetime import timedelta
from pathlib import Path

from .models import _LINE_FIELDS, Job
from .util import clean_block, clean_line, today

STATUSES = ("new", "saved", "applied", "oa", "interview", "offer", "rejected", "skipped")
TOUCHED = {"saved", "applied", "oa", "interview", "offer", "rejected"}  # never auto-pruned
DESCRIPTION_LIMIT = 4000
PRUNE_AFTER_DAYS = 45
STALE_AFTER_DAYS = 30


def _clean(rec: dict) -> dict:
    """Same cleaning as new jobs get (models.Job), for records stored before it existed or edited by hand."""
    for name in _LINE_FIELDS:
        if isinstance(rec.get(name), str):
            rec[name] = clean_line(rec[name])
    if isinstance(rec.get("description"), str):
        rec["description"] = clean_block(rec["description"])
    return rec


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.jobs: dict[str, dict] = {}
        self.rejected: dict[str, dict] = {}
        self.runs: list[dict] = []
        self.next_id = 1
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.jobs = {j["key"]: _clean(j) for j in raw.get("jobs", [])}
            self.rejected = raw.get("rejected", {})
            self.runs = raw.get("runs", [])
            self.next_id = raw.get("next_id", max((j["id"] for j in self.jobs.values()), default=0) + 1)
        self._by_id = {j["id"]: j for j in self.jobs.values()}

    # ------------------------------------------------------------------ queries
    def has(self, key: str) -> bool:
        return key in self.jobs or key in self.rejected

    def get(self, job_id: int) -> dict | None:
        return self._by_id.get(int(job_id))

    def all(self) -> list[dict]:
        return list(self.jobs.values())

    @staticmethod
    def score_of(rec: dict) -> int:
        ai = rec.get("ai") or {}
        return ai["fit"] if isinstance(ai.get("fit"), int) else rec.get("rule_score", 0)

    @staticmethod
    def visible(rec: dict) -> bool:
        """Open, not hidden by the AI as ineligible, not dismissed."""
        return (not rec.get("closed") and (rec.get("ai") or {}).get("eligible") != "no"
                and rec.get("status") not in ("skipped", "rejected"))

    def ranked(self, records: list[dict] | None = None) -> list[dict]:
        """Best first. rank_bonus (preferred city / remote, set from profile.yaml) lifts a job in the order
        without changing the score shown."""
        records = self.all() if records is None else records
        newest_first = sorted(records, key=lambda r: (r.get("posted_at") or "", r["id"]), reverse=True)
        return sorted(newest_first, key=lambda r: -(self.score_of(r) + (r.get("rank_bonus") or 0)))

    # ------------------------------------------------------------------ writes
    def add(self, job: Job, *, rule_score: int, signals: list[str], min_years: int | None) -> dict:
        now = today().isoformat()
        rec = {
            "id": self.next_id, "key": job.key, "source": job.source, "board": job.board,
            "company": job.company, "title": job.title, "url": job.url, "location": job.location,
            "remote": job.remote, "posted_at": job.posted_at, "expires": job.expires,
            "employment_type": job.employment_type, "salary": job.salary,
            "department": job.department, "level_hint": job.level_hint,
            "description": clean_block(job.description)[:DESCRIPTION_LIMIT],
            "first_seen": now, "last_seen": now, "closed": False,
            "rule_score": rule_score, "signals": signals, "min_years": min_years,
            "ai": None, "status": "new", "status_updated": now, "notes": "", "notified": False,
        }
        self.next_id += 1
        self.jobs[job.key] = rec
        self._by_id[rec["id"]] = rec
        self.rejected.pop(job.key, None)
        return rec

    def touch(self, key: str) -> None:
        rec = self.jobs.get(key)
        if rec:
            rec["last_seen"] = today().isoformat()
            rec["closed"] = False
        elif key in self.rejected:
            self.rejected[key]["last_seen"] = today().isoformat()

    def reject(self, key: str, reason: str) -> None:
        now = today().isoformat()
        self.rejected[key] = {"reason": reason, "date": now, "last_seen": now}

    def close_missing(self, fetched_boards: set[str], seen_keys: set[str]) -> int:
        """Mark jobs closed when their board was fetched successfully but no longer lists them."""
        closed = 0
        for key, rec in self.jobs.items():
            board = f"{rec['source']}:{rec['board']}"
            if board in fetched_boards and key not in seen_keys and not rec.get("closed"):
                rec["closed"] = True
                closed += 1
        return closed

    def close_expired(self, stale_days: int = STALE_AFTER_DAYS) -> int:
        """Close jobs whose board-provided expiry date has passed, and jobs no board has listed for a month (boards
        read only in part, like keyword searches, can't say a job closed, so this is their safety net)."""
        now, closed = today().isoformat(), 0
        stale = (today() - timedelta(days=stale_days)).isoformat()
        for rec in self.jobs.values():
            if rec.get("closed"):
                continue
            if (rec.get("expires") and rec["expires"] < now) or (rec.get("last_seen") or now) < stale:
                rec["closed"] = True
                closed += 1
        return closed

    def set_status(self, job_id: int, status: str, note: str | None = None) -> dict:
        if status not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}")
        rec = self.get(job_id)
        if rec is None:
            raise KeyError(f"no job with id {job_id}")
        rec["status"] = status
        rec["status_updated"] = today().isoformat()
        if note:
            rec["notes"] = (rec.get("notes") + "\n" if rec.get("notes") else "") + note
        return rec

    def record_run(self, stats: dict) -> None:
        self.runs = (self.runs + [stats])[-30:]

    def prune(self) -> None:
        cutoff = (today() - timedelta(days=PRUNE_AFTER_DAYS)).isoformat()
        for key in [k for k, r in self.jobs.items()
                    if r.get("closed") and r.get("last_seen", "") < cutoff and r.get("status") not in TOUCHED]:
            self._by_id.pop(self.jobs[key]["id"], None)
            del self.jobs[key]
        for rec in self.jobs.values():  # keep the file small: drop text nobody will read again
            if rec.get("closed") or (rec.get("ai") or {}).get("eligible") == "no":
                rec["description"] = ""
        self.rejected = {k: v for k, v in self.rejected.items() if v.get("last_seen", v.get("date", "")) >= cutoff}

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1, "next_id": self.next_id,
            "jobs": sorted(self.jobs.values(), key=lambda r: r["id"]),
            "rejected": dict(sorted(self.rejected.items())),
            "runs": self.runs,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
