from datetime import datetime, timedelta, timezone

import pytest

from jobpilot.config import parse_profile
from jobpilot.db import DB
from jobpilot.filters import prefilter, title_matches
from jobpilot.models import Job
from jobpilot.sources import aggregators, ats
from jobpilot.sources.http import html_to_text

PROFILE = parse_profile({
    "candidate": {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com", "phone": "555-0100",
                  "linkedin": "https://linkedin.com/in/ada", "resume_path": "/tmp/resume.txt"},
    "preferences": {"titles": ["software engineer", "backend engineer"], "title_exclude": ["intern", "manager"],
                    "locations": ["San Francisco", "Bay Area"], "remote_ok": True, "max_age_days": 21},
    "companies": ["Stripe", {"name": "NVIDIA", "careers_url": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"}],
    "answers": {"How did you hear": "Company careers page"},
})


@pytest.mark.parametrize("url,expected", [
    ("https://boards.greenhouse.io/stripe/jobs/123", ("greenhouse", {"slug": "stripe", "job_id": "123"})),
    ("https://job-boards.greenhouse.io/embed/job_app?for=figma&token=9", ("greenhouse", {"slug": "figma", "job_id": "9"})),
    ("https://stripe.com/jobs/listing/x?gh_jid=555", ("greenhouse", {"job_id": "555"})),
    ("https://jobs.lever.co/netflix/abc-123", ("lever", {"slug": "netflix", "job_id": "abc-123"})),
    ("https://jobs.ashbyhq.com/notion/uuid-1/application", ("ashby", {"slug": "notion", "job_id": "uuid-1"})),
    ("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x",
     ("workday", {"host": "nvidia.wd5.myworkdayjobs.com", "tenant": "nvidia", "site": "NVIDIAExternalCareerSite"})),
    ("https://example.com/careers", ("", {})),
])
def test_parse_ats_url(url, expected):
    assert ats.parse_ats_url(url) == expected


def test_title_matching():
    assert title_matches("Senior Software Engineer, Payments", PROFILE.titles, PROFILE.title_exclude)
    assert title_matches("Backend Engineer II", PROFILE.titles, PROFILE.title_exclude)
    assert not title_matches("Software Engineer Intern", PROFILE.titles, PROFILE.title_exclude)
    assert not title_matches("Engineering Manager", PROFILE.titles, PROFILE.title_exclude)
    assert not title_matches("Product Designer", PROFILE.titles, PROFILE.title_exclude)


def test_prefilter_location_and_age():
    base = dict(source="t", external_id="1", company="X", title="Software Engineer", url="u")
    assert prefilter(Job(**base, location="San Francisco, CA"), PROFILE)[0]
    assert prefilter(Job(**base, location="Remote - US"), PROFILE)[0]
    assert prefilter(Job(**base, location="London, UK"), PROFILE) == (False, "location")
    old = datetime.now(timezone.utc) - timedelta(days=60)
    assert prefilter(Job(**base, location="San Francisco", posted_at=old), PROFILE) == (False, "too old")


def test_greenhouse_parsing(monkeypatch):
    payload = {"jobs": [{"id": 42, "title": "Software Engineer", "location": {"name": "San Francisco"},
                         "absolute_url": "https://stripe.com/jobs/42", "updated_at": "2026-10-10T10:00:00-04:00",
                         "content": "&lt;p&gt;Build &lt;b&gt;payments&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Go&lt;/li&gt;&lt;/ul&gt;"}]}
    monkeypatch.setattr(ats, "get_json", lambda url, **k: payload)
    [job] = ats.greenhouse("Stripe", "stripe")
    assert job.ats == "greenhouse" and job.ats_meta == {"slug": "stripe", "job_id": "42"}
    assert "payments" in job.description and "• Go" in job.description
    assert job.apply_url.endswith("for=stripe&token=42")


def test_lever_and_ashby_parsing(monkeypatch):
    lever = [{"id": "abc", "text": "Backend Engineer", "categories": {"location": "Remote"}, "hostedUrl": "h",
              "applyUrl": "h/apply", "createdAt": 1760000000000, "descriptionPlain": "Desc", "lists": [],
              "workplaceType": "remote"}]
    monkeypatch.setattr(ats, "get_json", lambda url, **k: lever)
    [j] = ats.lever("Acme", "acme")
    assert j.remote is True and j.posted_at.year == 2025 and j.apply_url == "h/apply"
    ashby = {"jobs": [{"id": "u1", "title": "SWE", "location": "SF", "jobUrl": "https://jobs.ashbyhq.com/n/u1",
                       "descriptionPlain": "D", "publishedAt": "2026-10-01T00:00:00Z", "isRemote": False}]}
    monkeypatch.setattr(ats, "get_json", lambda url, **k: ashby)
    [j] = ats.ashby("Notion", "n")
    assert j.apply_url.endswith("/u1/application")


def test_linkedin_parsing_routes_to_ats():
    cards = aggregators.parse_linkedin_cards("""<ul><li><div class="base-card" data-entity-urn="urn:li:jobPosting:3999">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/swe-3999?trk=x"></a>
      <h3 class="base-search-card__title"> Software Engineer </h3><h4 class="base-search-card__subtitle">Figma</h4>
      <span class="job-search-card__location">San Francisco, CA</span><time datetime="2026-10-15"></time></div></li></ul>""")
    assert cards[0]["id"] == "3999" and cards[0]["company"] == "Figma"
    assert cards[0]["url"] == "https://www.linkedin.com/jobs/view/swe-3999"
    desc, ext = aggregators.parse_linkedin_detail(
        '<div class="show-more-less-html__markup"><p>Great job</p></div>'
        '<code id="applyUrl" style="display:none"><!--"https://www.linkedin.com/jobs/view/externalApply/3999?'
        'url=https%3A%2F%2Fboards%2Egreenhouse%2Eio%2Ffigma%2Fjobs%2F777&urlHash=x"--></code>')
    assert desc == "Great job"
    job = Job(source="linkedin", external_id="3999", company="Figma", title="SWE", url="u")
    aggregators._route_external(job, ext)
    assert job.ats == "greenhouse" and job.apply_url.endswith("for=figma&token=777")


def test_db_dedupes_across_sources(tmp_path):
    db = DB(tmp_path / "t.db")
    a = Job(source="greenhouse", external_id="s-1", company="Stripe", title="Software Engineer", url="u",
            location="San Francisco, CA")
    b = Job(source="linkedin", external_id="999", company="Stripe", title="Software Engineer", url="v",
            location="San Francisco Bay Area")
    assert db.insert_job(a) is True
    assert db.insert_job(b) is False  # same job seen via LinkedIn
    db.update_job(a.uid, status="review", score=88, assessment={"summary": "x"})
    got = db.get_job(a.uid)
    assert got["assessment"]["summary"] == "x" and db.counts()["review"] == 1


def test_html_to_text():
    assert html_to_text("<p>a</p><p>b</p>") == "a\n\nb"
