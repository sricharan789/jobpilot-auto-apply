"""Broad-market discovery: Adzuna (aggregates Indeed-style listings via an official API),
RemoteOK, and LinkedIn's public guest job search.

LinkedIn and Indeed prohibit automated applying, so nothing here ever applies on those sites.
LinkedIn is used read-only, without logging in, at a low request rate. When a LinkedIn
listing links out to the company's own ATS, the job is re-routed there so it can be
auto-filled; "Easy Apply" jobs go to your review queue as manual."""
from __future__ import annotations

import logging
import random
import time
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from bs4 import BeautifulSoup

from ..models import Job
from .ats import parse_ats_url
from .http import get_json, html_to_text, parse_dt, request

log = logging.getLogger(__name__)


def _route_external(job: Job, external_url: str) -> None:
    """Point a job at the company ATS it links to, so the form filler can handle it."""
    ats, meta = parse_ats_url(external_url)
    job.apply_url = external_url
    if ats:
        job.ats, job.ats_meta = ats, meta
        if ats == "greenhouse" and meta.get("slug") and meta.get("job_id"):
            job.apply_url = (f"https://job-boards.greenhouse.io/embed/job_app?for={meta['slug']}"
                             f"&token={meta['job_id']}")
    else:
        job.ats = "external"


# ---------------------------------------------------------------- Adzuna


def adzuna(app_id: str, app_key: str, keywords: list[str], where: str, max_age_days: int,
           country: str = "us") -> list[Job]:
    if not (app_id and app_key):
        return []
    jobs = []
    for kw in keywords:
        url = (f"https://api.adzuna.com/v1/api/jobs/{country}/search/1?app_id={app_id}&app_key={app_key}"
               f"&results_per_page=50&what={quote_plus(kw)}&where={quote_plus(where)}"
               f"&max_days_old={max_age_days}&sort_by=date&content-type=application/json")
        for j in (get_json(url) or {}).get("results", []):
            sal = ""
            if j.get("salary_min"):
                sal = f"{int(j['salary_min'])}-{int(j.get('salary_max') or j['salary_min'])} USD"
            job = Job(
                source="adzuna", external_id=str(j["id"]),
                company=(j.get("company") or {}).get("display_name", ""),
                title=j.get("title", "").replace("<strong>", "").replace("</strong>", ""),
                location=(j.get("location") or {}).get("display_name", ""),
                url=j.get("redirect_url", ""), description=html_to_text(j.get("description", "")),
                posted_at=parse_dt(j.get("created")), ats="external", salary=sal,
                ats_meta={"description_is_snippet": True},
            )
            jobs.append(job)
    return jobs


# ---------------------------------------------------------------- RemoteOK


def remoteok(keywords: list[str]) -> list[Job]:
    data = get_json("https://remoteok.com/api")
    if not isinstance(data, list):
        return []
    kws = [k.lower() for k in keywords]
    jobs = []
    for j in data[1:]:  # first element is the API's legal notice
        hay = f"{j.get('position', '')} {' '.join(j.get('tags') or [])}".lower()
        if kws and not any(k in hay for k in kws):
            continue
        job = Job(
            source="remoteok", external_id=str(j.get("id")), company=j.get("company", ""),
            title=j.get("position", ""), location=j.get("location") or "Remote",
            url=j.get("url", ""), description=html_to_text(j.get("description", "")),
            posted_at=parse_dt(j.get("date")), remote=True, ats="external",
            salary=f"{j['salary_min']}-{j.get('salary_max')} USD" if j.get("salary_min") else "",
        )
        _route_external(job, j.get("apply_url") or job.url)
        jobs.append(job)
    return jobs


# ---------------------------------------------------------------- LinkedIn (read-only)

LI_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def _pause() -> None:
    time.sleep(random.uniform(2.0, 4.5))


def parse_linkedin_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("li"):
        div = card.select_one("[data-entity-urn]")
        if not div:
            continue
        jid = div["data-entity-urn"].rsplit(":", 1)[-1]
        title = card.select_one(".base-search-card__title")
        company = card.select_one(".base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        when = card.select_one("time")
        link = card.select_one("a.base-card__full-link")
        out.append({
            "id": jid,
            "title": title.get_text(strip=True) if title else "",
            "company": company.get_text(strip=True) if company else "",
            "location": loc.get_text(strip=True) if loc else "",
            "posted": when.get("datetime") if when else None,
            "url": (link["href"].split("?")[0] if link else f"https://www.linkedin.com/jobs/view/{jid}"),
        })
    return out


def parse_linkedin_detail(html: str) -> tuple[str, str]:
    """Returns (description_text, external_apply_url or '')."""
    soup = BeautifulSoup(html, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup") or soup.select_one(".description__text")
    text = html_to_text(str(desc)) if desc else ""
    external = ""
    code = soup.select_one("code#applyUrl")
    if code:
        raw = code.decode_contents().strip().strip("<!-->").strip().strip('"')
        q = parse_qs(urlparse(raw).query)
        external = unquote(q["url"][0]) if "url" in q else raw
    return text, external


def linkedin(keywords: list[str], location: str, max_age_days: int, max_per_keyword: int = 50,
             is_relevant=None) -> list[Job]:
    jobs = []
    seconds = max(1, max_age_days) * 86400
    for kw in keywords:
        cards: list[dict] = []
        for start in range(0, max_per_keyword, 25):
            url = (f"{LI_SEARCH}?keywords={quote_plus(kw)}&location={quote_plus(location)}"
                   f"&f_TPR=r{seconds}&start={start}")
            r = request("GET", url, retries=1)
            _pause()
            if r is None:
                break
            page = parse_linkedin_cards(r.text)
            cards.extend(page)
            if len(page) < 10:
                break
        for c in cards:
            job = Job(source="linkedin", external_id=c["id"], company=c["company"], title=c["title"],
                      location=c["location"], url=c["url"], posted_at=parse_dt(c["posted"]),
                      ats="linkedin_easy_apply")
            # Only fetch details for jobs that already pass the cheap title/location filter
            if is_relevant is not None and not is_relevant(job):
                jobs.append(job)
                continue
            r = request("GET", LI_DETAIL.format(id=c["id"]), retries=1)
            _pause()
            if r is not None:
                job.description, external = parse_linkedin_detail(r.text)
                if external:
                    _route_external(job, external)
            jobs.append(job)
    return jobs
