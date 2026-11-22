"""Your config vs the repo's template, `jobradar init`, and a guard that personal files stay out of git."""

import stat
from pathlib import Path

import yaml

from jobradar.config import init_files, load_settings

ROOT = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "profile.example.yaml").write_text("graduation_year: 2027\nskills: []\n")
    (tmp_path / ".env.example").write_text("GEMINI_API_KEY=\n")
    return tmp_path


def test_template_is_used_until_you_have_your_own_profile(tmp_path, monkeypatch):
    monkeypatch.delenv("JOBRADAR_HOME", raising=False)
    root = _project(tmp_path)
    s = load_settings(root)
    assert s.profile_from == "config/profile.example.yaml" and not s.own_profile
    assert s.profile["graduation_year"] == 2027 and s.profile["salary"]["currency"] == "INR"  # defaults filled in
    (root / "config" / "profile.yaml").write_text("graduation_year: 2025\nlocations: {preferred: [pune]}\n")
    s = load_settings(root)
    assert s.own_profile and s.profile["graduation_year"] == 2025
    assert s.profile["locations"]["preferred"] == ["pune"] and s.profile["locations"]["accept"] == ["india"]


def test_init_creates_your_files_and_never_overwrites(tmp_path):
    root = _project(tmp_path)
    done = dict(init_files(root))
    assert done == {"config/profile.yaml": "created from config/profile.example.yaml",
                    ".env": "created from .env.example"}
    assert (root / "config" / "profile.yaml").read_text() == "graduation_year: 2027\nskills: []\n"
    assert stat.S_IMODE((root / ".env").stat().st_mode) == 0o600
    assert (root / "profile").is_dir() and (root / "data").is_dir()
    (root / "config" / "profile.yaml").write_text("graduation_year: 2025\n")
    assert dict(init_files(root))["config/profile.yaml"] == "already there, left as is"
    assert (root / "config" / "profile.yaml").read_text() == "graduation_year: 2025\n"


def test_shipped_template_is_generic_and_valid():
    template = yaml.safe_load((ROOT / "config" / "profile.example.yaml").read_text())
    assert template["name"] == "" and template["summary"] == "" and template["skills"] == []
    assert template["locations"]["preferred"] == [] and template["salary"]["min"] == 0
    assert template["roles"] and template["exclude_titles"] and template["ai"]["models"]


def test_personal_files_are_git_ignored():
    rules = (ROOT / ".gitignore").read_text().splitlines()
    for personal in (".env", "config/profile.yaml", "profile/*", "data/"):
        assert personal in rules, personal
    assert "!profile/README.md" in rules
