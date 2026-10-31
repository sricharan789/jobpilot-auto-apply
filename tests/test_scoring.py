"""Scorer gate logic and the drafter-reviewer loop, with the Anthropic API mocked."""
from pathlib import Path

import pytest

from jobpilot.apply.coverletter import verify_pdf_text, write_cover_letter_pdf
from jobpilot.config import load_settings, parse_profile

PROFILE = parse_profile({
    "candidate": {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com",
                  "phone": "555-0100", "linkedin": "https://linkedin.com/in/ada", "resume_path": "/tmp/r.txt",
                  "work_authorization": "H-1B", "requires_sponsorship": True},
})
JOB = {"uid": "t:1", "company": "Acme", "title": "Software Engineer", "location": "SF", "description": "Build things."}


def make_scorer(monkeypatch, assess_input, review_input=None):
    """Patch the Anthropic client so _call returns canned tool inputs in order."""
    import jobpilot.scoring as scoring
    calls = {"n": 0}
    inputs = [assess_input] + ([review_input] if review_input else [])

    class Block:
        type = "tool_use"
        def __init__(self, inp): self.input = inp

    class Msg:
        def __init__(self, inp): self.content = [Block(inp)]

    class Messages:
        def create(self, **kw):
            inp = inputs[min(calls["n"], len(inputs) - 1)]
            calls["n"] += 1
            return Msg(inp)

    class Client:
        def __init__(self, **kw): self.messages = Messages()

    monkeypatch.setattr(scoring.anthropic, "Anthropic", Client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return scoring.Scorer(load_settings(), PROFILE, "Ada Lovelace. Python engineer."), calls


BASE = dict(eligibility_gate="pass", language_gate="pass", technical_skills=80, experience_match=75,
            culture_fit=70, career_alignment=72, location_logistics="pass", score=78, verdict="good",
            summary="Good fit.", matched=["Python"], gaps=[], red_flags=[], dealbreaker=False,
            cover_letter="I build reliable backends and want to solve Acme's scaling work.")


def test_eligibility_fail_zeroes_score(monkeypatch):
    scorer, _ = make_scorer(monkeypatch, {**BASE, "score": 85, "eligibility_gate": "fail",
                                          "eligibility_note": "US citizenship required"})
    r = scorer.assess(JOB, review=False)
    assert r["score"] == 0 and r["dealbreaker"] is True
    assert any("Hard gate failed" in f for f in r["red_flags"])


def test_language_fail_zeroes_score(monkeypatch):
    scorer, _ = make_scorer(monkeypatch, {**BASE, "score": 90, "language_gate": "fail",
                                          "language_note": "fluent German required"})
    assert scorer.assess(JOB, review=False)["score"] == 0


def test_relocation_fail_zeroes_score(monkeypatch):
    scorer, _ = make_scorer(monkeypatch, {**BASE, "score": 88, "location_logistics": "fail"})
    assert scorer.assess(JOB, review=False)["score"] == 0


def test_pass_keeps_score_and_runs_reviewer(monkeypatch):
    review = {"verdict": "revise", "issues": ["Opening is generic"],
              "revised_cover_letter": "A" * 200}
    scorer, calls = make_scorer(monkeypatch, dict(BASE), review)
    r = scorer.assess(JOB, review=True)
    assert r["score"] == 78
    assert calls["n"] == 2  # assess + review
    assert r["cover_letter"] == "A" * 200
    assert r["review_verdict"] == "revise" and r["review_issues"] == ["Opening is generic"]


def test_reviewer_ship_keeps_draft(monkeypatch):
    review = {"verdict": "ship", "issues": [], "revised_cover_letter": BASE["cover_letter"]}
    scorer, _ = make_scorer(monkeypatch, dict(BASE), review)
    r = scorer.assess(JOB, review=True)
    assert r["cover_letter"] == BASE["cover_letter"] and r["review_verdict"] == "ship"


def test_failed_gate_skips_reviewer(monkeypatch):
    scorer, calls = make_scorer(monkeypatch, {**BASE, "eligibility_gate": "fail", "eligibility_note": "citizen only"})
    scorer.assess(JOB, review=True)
    assert calls["n"] == 1  # reviewer not called on a zeroed job


def test_cover_letter_pdf_text_verifies(tmp_path):
    job = {"company": "Acme", "cover_letter": "I build reliable backends.\n\nI would welcome a conversation."}
    pdf = write_cover_letter_pdf(tmp_path, job, PROFILE)
    ok, msg = verify_pdf_text(pdf, ["Ada", "ada@example.com"])
    assert ok, msg
    bad, msg = verify_pdf_text(pdf, ["nonexistent@nowhere.xyz"])
    assert not bad and "did not survive" in msg


def test_cover_letter_pdf_empty_flagged(tmp_path):
    pytest.importorskip("pypdf")
    # A near-empty cover letter body should be caught by the minimum-text guard.
    job = {"company": "Acme", "cover_letter": "x"}
    pdf = write_cover_letter_pdf(tmp_path, job, PROFILE)
    ok, msg = verify_pdf_text(pdf, ["Ada"])
    assert not ok and "almost no text" in msg
