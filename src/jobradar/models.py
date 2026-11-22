from __future__ import annotations

from dataclasses import dataclass, field


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
    needs_detail: bool = False  # description must be fetched separately (Workday, Eightfold, ...)
    detail: dict = field(default_factory=dict, repr=False)

    @property
    def key(self) -> str:
        return f"{self.source}:{self.board}:{self.native_id}"
