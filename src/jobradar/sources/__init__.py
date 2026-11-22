"""Job sources. Each module exposes `async fetch(http, target) -> list[Job]` and optionally
`async enrich(http, job)` to fetch the full description for boards whose list endpoint lacks it.

`COMPLETE_LISTING = False` on a module means its fetch returns only the newest slice of the board, so a job
missing from one run is NOT treated as closed (expiry dates are used instead)."""

from __future__ import annotations

from dataclasses import dataclass, field

from . import (amazon, ashby, atlassian, eightfold, google, greenhouse, jsonld, lever, oracle, remote,
               rippling, smartrecruiters, unstop, wellfound, workable, workday)

MODULES = {
    "greenhouse": greenhouse,
    "lever": lever,
    "ashby": ashby,
    "smartrecruiters": smartrecruiters,
    "workable": workable,
    "rippling": rippling,
    "workday": workday,
    "amazon": amazon,
    "eightfold": eightfold,
    "oracle": oracle,
    "google": google,
    "atlassian": atlassian,
    "unstop": unstop,
    "wellfound": wellfound,
    "remote": remote,
    "scrape": jsonld,
}
SLUG_SOURCES = ("greenhouse", "lever", "ashby", "smartrecruiters", "workable", "rippling")


@dataclass
class Target:
    source: str
    board: str
    name: str
    opts: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.source}:{self.board}"


def complete_listing(source: str) -> bool:
    return getattr(MODULES[source], "COMPLETE_LISTING", True)


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in text.lower()).strip("-")


def localize(targets: list[Target], country: str) -> list[Target]:
    """Search career sites that filter by country (Workday, Oracle, Eightfold, Amazon, Google, Wellfound, Jobicy) in
    your country (profile locations.country). A `country:` set on a source in sources.yaml wins."""
    for t in targets:
        t.opts.setdefault("country", country or "")
    return targets


def build_targets(cfg: dict, only: list[str] | None = None) -> list[Target]:
    """Turn config/sources.yaml into fetch targets. `only` filters by source name, board or company."""
    targets: list[Target] = []
    for source in SLUG_SOURCES:
        for board, name in (cfg.get(source) or {}).items():
            targets.append(Target(source, str(board), str(name or board)))
    for entry in cfg.get("workday") or []:
        targets.append(Target("workday", entry["tenant"], entry.get("name") or entry["tenant"], dict(entry)))
    if cfg.get("amazon") is not None:
        targets.append(Target("amazon", "amazon", "Amazon", dict(cfg.get("amazon") or {})))
    for entry in cfg.get("eightfold") or []:
        opts = dict(entry)
        opts.setdefault("queries", cfg.get("eightfold_queries"))
        targets.append(Target("eightfold", entry["domain"].split(".")[0], entry.get("name") or entry["domain"], opts))
    for entry in cfg.get("oracle") or []:
        targets.append(Target("oracle", entry["host"].split(".")[0], entry.get("name") or entry["host"], dict(entry)))
    for source in ("google", "atlassian", "unstop", "wellfound"):
        if cfg.get(source) is not None and cfg.get(source) is not False:
            opts = cfg.get(source) if isinstance(cfg.get(source), dict) else {}
            targets.append(Target(source, source, opts.get("name") or source.capitalize(), dict(opts)))
    for board in cfg.get("remote") or []:
        targets.append(Target("remote", board, board.capitalize(), {}))
    for entry in cfg.get("scrape") or []:
        targets.append(Target("scrape", entry.get("board") or _slug(entry["name"]), entry["name"], dict(entry)))
    if only:
        wanted = {o.lower() for o in only}
        targets = [t for t in targets if {t.source.lower(), t.board.lower(), t.name.lower()} & wanted]
    return targets


async def fetch(http, target: Target):
    return await MODULES[target.source].fetch(http, target)


async def enrich(http, job) -> None:
    fn = getattr(MODULES[job.source], "enrich", None)
    if fn is not None:
        await fn(http, job)
