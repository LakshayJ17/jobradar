"""Settings: config/profile.yaml + config/sources.yaml + .env (never printed).

config/profile.yaml is yours and git-ignored; `jobradar init` creates it from config/profile.example.yaml, the
template that ships with the repo. Until it exists, the template is used.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import salary

DEFAULT_PROFILE: dict = {
    "name": "",
    "graduation_year": 2026,
    "degree": "B.Tech CSE",
    "resume_file": "profile/resume.pdf",
    "summary": "",
    "skills": [],
    "experience_years": 0,              # your own full-time experience; 0 = fresher
    "max_years_required": 1,            # drop jobs asking for more years than this
    "min_years_required": 0,            # drop jobs asking for fewer years than this (0 = off)
    "exclude_levels": ["senior", "director", "executive"],   # board level labels to drop
    "looking_for": "",                  # plain-English role description for the AI ('' = from the role families)
    "roles": {"software": [r"software (development )?engineer", r"developer"]},
    "exclude_titles": [r"senior|lead|staff|principal|manager|director"],
    "exclude_description": [],
    "include_internships": False,
    "max_age_days": 30,
    "locations": {"country": "India", "accept": ["india"], "preferred": [], "preferred_boost": 10,
                  "remote_is_preferred": True, "remote": True},
    "salary": {"currency": "INR", "hide_unknown": False},   # floor: salary.min (or the older salary.min_lpa)
    "ai": {
        "enabled": True,
        "models": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemma-4-31b-it"],
        "draft_models": ["gemini-3.5-flash", "gemini-3.5-flash-lite"],
        "max_jobs_per_run": 80,
        "batch_size": 6,
        "rpm": 10,
    },
    "notify": {"top_n": 15, "min_score": 55, "telegram": True, "email": True},
}


PROFILE_FILE = "profile.yaml"           # yours, git-ignored
EXAMPLE_FILE = "profile.example.yaml"   # the template in the repo


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


def find_root(start: Path | None = None) -> Path:
    if os.environ.get("JOBRADAR_HOME"):
        return Path(os.environ["JOBRADAR_HOME"]).expanduser().resolve()
    cur = (start or Path.cwd()).resolve()
    for p in (cur, *cur.parents):
        if any((p / "config" / name).exists() for name in (PROFILE_FILE, EXAMPLE_FILE, "sources.yaml")):
            return p
    return Path(__file__).resolve().parents[2]  # repo checkout: src/jobradar/config.py -> repo root


def load_dotenv(path: Path) -> None:
    """Tiny .env reader. Existing environment variables win; values are never logged."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()
        if key and value and key not in os.environ:
            os.environ[key] = value


@dataclass
class Settings:
    root: Path
    profile: dict
    sources: dict
    profile_from: str = "built-in defaults"   # which file the profile came from (shown by `jobradar doctor`)

    @property
    def own_profile(self) -> bool:
        return self.profile_from == f"config/{PROFILE_FILE}"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def store_path(self) -> Path:
        return self.data_dir / "jobs.json"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def drafts_dir(self) -> Path:
        return self.data_dir / "drafts"


def load_settings(root: Path | None = None) -> Settings:
    root = root or find_root()
    load_dotenv(root / ".env")
    profile, source = {}, "built-in defaults"
    for name in (PROFILE_FILE, EXAMPLE_FILE):
        path = root / "config" / name
        if path.exists():
            profile, source = yaml.safe_load(path.read_text(encoding="utf-8")) or {}, f"config/{name}"
            break
    sources_path = root / "config" / "sources.yaml"
    sources = yaml.safe_load(sources_path.read_text(encoding="utf-8")) if sources_path.exists() else {}
    merged = _merge(DEFAULT_PROFILE, profile)
    salary.configure(merged)  # pay is shown in your currency everywhere (list, report, digest)
    return Settings(root=root, profile=merged, sources=sources or {}, profile_from=source)


def init_files(root: Path) -> list[tuple[str, str]]:
    """Create your own config/profile.yaml and .env from the templates. Never overwrites. Returns (path, action)."""
    done = []
    for target, template, private in ((f"config/{PROFILE_FILE}", f"config/{EXAMPLE_FILE}", False),
                                      (".env", ".env.example", True)):
        dst, src = root / target, root / template
        if dst.exists():
            done.append((target, "already there, left as is"))
        elif src.exists():
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            if private:
                dst.chmod(0o600)  # API keys and tokens: readable by you only
            done.append((target, f"created from {template}"))
        else:
            done.append((target, f"skipped: {template} is missing"))
    for folder in ("profile", "data"):  # your resume and tracker: readable by you only
        (root / folder).mkdir(exist_ok=True)
        (root / folder).chmod(0o700)
    return done


def resume_text(settings: Settings) -> tuple[str, str]:
    """Return (text, where_it_came_from). Order: resume file -> JOBRADAR_RESUME_TEXT -> profile summary."""
    p = settings.profile
    candidates = []
    if p.get("resume_file"):
        f = settings.root / p["resume_file"]
        candidates += [f] + [f.with_suffix(s) for s in (".pdf", ".txt", ".md") if f.with_suffix(s) != f]
        if f.parent.is_dir():  # any other resume dropped in the folder, e.g. "Jane_Doe_Resume (1).pdf", newest first
            others = [x for x in f.parent.iterdir() if x.suffix.lower() in (".pdf", ".txt", ".md")
                      and x not in candidates and not x.name.startswith(".") and x.name.lower() != "readme.md"]
            candidates += sorted(others, key=lambda x: x.stat().st_mtime, reverse=True)
    unreadable = []
    for f in candidates:
        if not f.exists():
            continue
        if f.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            text = "\n".join((page.extract_text() or "") for page in PdfReader(str(f)).pages)
        else:
            text = f.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            return text.strip(), str(f.relative_to(settings.root))
        unreadable.append(str(f.relative_to(settings.root)))
    if os.environ.get("JOBRADAR_RESUME_TEXT", "").strip():
        return os.environ["JOBRADAR_RESUME_TEXT"].strip(), "JOBRADAR_RESUME_TEXT"
    fallback = f"{p.get('degree', '')}, graduating {p.get('graduation_year', '')}.\n{p.get('summary', '')}\n" \
               f"Skills: {', '.join(p.get('skills') or [])}"
    if unreadable:
        return fallback.strip(), (f"profile.yaml summary - {', '.join(unreadable)} has no extractable text "
                                  "(image-only PDF?); save profile/resume.txt")
    return fallback.strip(), "profile.yaml summary (add profile/resume.pdf for better matching)"
