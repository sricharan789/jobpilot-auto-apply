"""Runs the real form filler in headless Chromium against a local mock application form."""
from pathlib import Path

import pytest

from jobpilot.apply.engine import Filler
from jobpilot.config import load_settings, parse_profile

FORM = Path(__file__).parent / "fixtures" / "mock_greenhouse_form.html"


class FakeScorer:
    def __init__(self):
        self.asked = []

    def answer_fields(self, job, fields):
        self.asked = fields
        out = {}
        for f in fields:
            lab = f["label"].lower()
            if "sponsorship" in lab:
                out[f["key"]] = {"value": "No", "confident": True}
            elif "authorized" in lab:
                out[f["key"]] = {"value": "Yes", "confident": True}
            elif "privacy" in lab:
                out[f["key"]] = {"value": "Yes", "confident": True}
            elif "why" in lab:
                out[f["key"]] = {"value": "I love payments infrastructure.", "confident": True}
            else:
                out[f["key"]] = {"value": "", "confident": False}
        return out


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    resume = tmp_path / "resume.txt"
    resume.write_text("Ada Lovelace. Engineer.")
    profile = parse_profile({
        "candidate": {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
                      "phone": "555-0100", "linkedin": "https://linkedin.com/in/ada", "resume_path": str(resume)},
        "answers": {"How did you hear": "Company careers page"},
    })
    job = {"uid": "test:1", "company": "Stripe", "title": "SWE", "url": FORM.as_uri(),
           "apply_url": FORM.as_uri(), "ats": "greenhouse", "cover_letter": "Dear team"}
    return load_settings(), profile, job


def test_prepare_then_submit(setup, monkeypatch):
    settings, profile, job = setup
    scorer = FakeScorer()
    filler = Filler(settings, profile, scorer)

    prep = filler.run(job, submit=False)
    assert prep.ok and prep.stage == "prepared", prep.message
    vals = {a["label"]: a["value"] for a in prep.answers.values()}
    assert vals["First Name"] == "Ada" and vals["Email"] == "ada@example.com"
    assert vals["How did you hear about this job?"] == "Company careers page"
    assert vals["Resume/CV"] == "<resume>"
    # Only the non-standard questions went to Claude
    assert {f["label"] for f in scorer.asked} >= {"Why do you want to work here?"}
    assert "First Name" not in {f["label"] for f in scorer.asked}
    assert (settings.screenshots_dir / prep.screenshot).exists()

    # User edits an answer in the dashboard, then approves. Saved answers are reused; Claude isn't called.
    saved = dict(prep.answers)
    key = next(k for k, a in saved.items() if a["label"].startswith("Why do you want"))
    saved[key] = {**saved[key], "value": "Edited by me"}
    settings.dry_run = False
    scorer.asked = []
    done = filler.run(job, submit=True, saved=saved)
    assert done.stage == "submitted", done.message
    assert scorer.asked == []


def test_dry_run_blocks_submit(setup):
    settings, profile, job = setup
    settings.dry_run = True
    r = Filler(settings, profile, FakeScorer()).run(job, submit=True)
    assert r.stage == "attention" and "DRY_RUN" in r.message
