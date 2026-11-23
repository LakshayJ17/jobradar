from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta

from .util import clean_block, clean_line, today

_LINE_FIELDS = ("source", "board", "company", "native_id", "title", "url", "location", "employment_type",
                "department", "level_hint")


@dataclass
class Job:
    """One posting, normalised across all job boards."""

    source: str  # greenhouse | lever | ashby | smartrecruiters | workable | workday | amazon | eightfold
    board: str  # company slug / tenant on that source
    company: str  # display name
    native_id: str  # id on the source
    title: str
    url: str
    location: str = ""
    remote: bool | None = None
    posted_at: str | None = None  # YYYY-MM-DD
    expires: str | None = None  # YYYY-MM-DD, when the board says (closed automatically after this)
    description: str = ""  # plain text
    employment_type: str = ""
    department: str = ""
    level_hint: str = ""  # e.g. SmartRecruiters experienceLevel, Amazon "university"
    salary: list[float] | None = None  # yearly [min, max] in INR lakhs (canonical); shown in your currency
    accepting: bool | None = None  # the board says applications are open (True) / closed (False); None: not said
    needs_detail: bool = False  # description must be fetched separately (Workday, Eightfold, ...)
    detail: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        for name in _LINE_FIELDS:
            setattr(self, name, clean_line(getattr(self, name)))
        self.description = clean_block(self.description)
        for name in ("posted_at", "expires"):
            value = getattr(self, name)
            if not (isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)):
                setattr(self, name, None)
        if self.posted_at and self.posted_at > (today() + timedelta(days=2)).isoformat():
            self.posted_at = None  # a date in the future would rank a posting as brand new forever

    @property
    def key(self) -> str:
        return f"{self.source}:{self.board}:{self.native_id}"
