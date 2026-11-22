from pathlib import Path

import pytest
import yaml

from jobradar.config import DEFAULT_PROFILE, _merge
from jobradar.models import Job
from jobradar.util import today

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def profile() -> dict:
    """The template that ships with the repo (config/profile.example.yaml), so tests guard the defaults every new
    user starts from, and never depend on anyone's own git-ignored config/profile.yaml."""
    return _merge(DEFAULT_PROFILE, yaml.safe_load((ROOT / "config" / "profile.example.yaml").read_text()))


@pytest.fixture
def make_job():
    def _make(**kw) -> Job:
        base = dict(source="greenhouse", board="acme", company="Acme", native_id="1", title="Software Engineer",
                    url="https://example.com/job/1", location="Bengaluru, India", posted_at=today().isoformat(),
                    description="We build things in Python.")
        base.update(kw)
        return Job(**base)
    return _make
