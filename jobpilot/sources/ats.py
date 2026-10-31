"""Company applicant-tracking-system boards. These are public, documented JSON feeds
that companies publish for their own careers pages, so they are the most reliable source."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from ..models import Job
from .http import get_json, html_to_text, parse_dt, request

log = logging.getLogger(__name__)


# ---------------------------------------------------------------- URL parsing


def parse_ats_url(url: str) -> tuple[str, dict]:
    """Identify the ATS behind a job or careers URL. Returns (ats, meta) or ("", {})."""
    if not url:
        return "", {}
    u = urlparse(url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    qs = parse_qs(u.query)

    if "greenhouse.io" in host:
        if "for" in qs:  # embed URLs: ?for=slug&token=id
            meta = {"slug": qs["for"][0]}
            if "token" in qs:
                meta["job_id"] = qs["token"][0]
            return "greenhouse", meta
        if parts and parts[0] not in {"embed", "v1"}:
            meta = {"slug": parts[0]}
            if len(parts) >= 3 and parts[1] == "jobs":
                meta["job_id"] = parts[2]
            return "greenhouse", meta
    if "gh_jid" in qs:  # company-hosted page backed by Greenhouse
        return "greenhouse", {"job_id": qs["gh_jid"][0]}
    if host.endswith("lever.co") and parts:
        meta = {"slug": parts[0]}
        if len(parts) >= 2:
            meta["job_id"] = parts[1]
        return "lever", meta
    if host.endswith("ashbyhq.com") and parts:
        meta = {"slug": parts[0]}
        if len(parts) >= 2:
            meta["job_id"] = parts[1]
        return "ashby", meta
    if "myworkdayjobs.com" in host:
        tenant = host.split(".")[0]
        site_parts = [p for p in parts if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)]
        if site_parts:
            return "workday", {"host": host, "tenant": tenant, "site": site_parts[0]}
    if host.endswith("smartrecruiters.com") and parts:
        meta = {"slug": parts[0]}
        if len(parts) >= 2:
            meta["job_id"] = parts[1].split("-")[0]
        return "smartrecruiters", meta
    return "", {}


def _recent(dt: datetime | None, max_age_days: int) -> bool:
    return dt is None or dt >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


# ---------------------------------------------------------------- fetchers


def greenhouse(company: str, slug: str) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
    jobs = []
    for j in (data or {}).get("jobs", []):
        jid = str(j["id"])
        jobs.append(Job(
            source="greenhouse", external_id=f"{slug}-{jid}", company=company,
            title=j.get("title", ""), location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            # The embed form is the most stable page for automated filling
            apply_url=f"https://job-boards.greenhouse.io/embed/job_app?for={slug}&token={jid}",
            description=html_to_text(j.get("content", "")),
            posted_at=parse_dt(j.get("first_published") or j.get("updated_at")),
            ats="greenhouse", ats_meta={"slug": slug, "job_id": jid},
        ))
    return jobs


def lever(company: str, slug: str) -> list[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    jobs = []
    for j in data or []:
        cats = j.get("categories") or {}
        desc = j.get("descriptionPlain", "")
        for lst in j.get("lists") or []:
            desc += f"\n\n{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}"
        desc += "\n\n" + (j.get("additionalPlain") or "")
        sal = j.get("salaryRange") or {}
        jobs.append(Job(
            source="lever", external_id=f"{slug}-{j['id']}", company=company,
            title=j.get("text", ""), location=cats.get("location", ""),
            url=j.get("hostedUrl", ""), apply_url=j.get("applyUrl", ""),
            description=desc.strip(), posted_at=parse_dt(j.get("createdAt")),
            ats="lever", ats_meta={"slug": slug, "job_id": j["id"]},
            salary=f"{sal.get('min', '')}-{sal.get('max', '')} {sal.get('currency', '')}".strip(" -") if sal else "",
            remote=(j.get("workplaceType") == "remote") if j.get("workplaceType") else None,
        ))
    return jobs


def ashby(company: str, slug: str) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true")
    jobs = []
    for j in (data or {}).get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary", "")
        jobs.append(Job(
            source="ashby", external_id=f"{slug}-{j['id']}", company=company,
            title=j.get("title", ""), location=j.get("location", ""),
            url=j.get("jobUrl", ""), apply_url=j.get("applyUrl") or f"{j.get('jobUrl', '')}/application",
            description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
            posted_at=parse_dt(j.get("publishedAt")), ats="ashby",
            ats_meta={"slug": slug, "job_id": j["id"]}, salary=comp, remote=j.get("isRemote"),
        ))
    return jobs


def smartrecruiters(company: str, slug: str) -> list[Job]:
    jobs, offset = [], 0
    while offset < 500:
        data = get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}")
        content = (data or {}).get("content", [])
        for j in content:
            loc = j.get("location") or {}
            jobs.append(Job(
                source="smartrecruiters", external_id=f"{slug}-{j['id']}", company=company,
                title=j.get("name", ""),
                location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                url=f"https://jobs.smartrecruiters.com/{slug}/{j['id']}",
                posted_at=parse_dt(j.get("releasedDate")), ats="smartrecruiters",
                ats_meta={"slug": slug, "job_id": j["id"], "needs_detail": True},
                remote=loc.get("remote"),
            ))
        if len(content) < 100:
            break
        offset += 100
    return jobs


def smartrecruiters_detail(job: Job) -> None:
    m = job.ats_meta
    d = get_json(f"https://api.smartrecruiters.com/v1/companies/{m['slug']}/postings/{m['job_id']}")
    if d:
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        job.description = "\n\n".join(html_to_text(s.get("text", "")) for s in sections.values() if isinstance(s, dict))
        job.apply_url = d.get("applyUrl") or job.url


def workday(company: str, host: str, tenant: str, site: str, search_terms: list[str],
            max_age_days: int = 30) -> list[Job]:
    """Workday's careers sites are backed by a JSON endpoint. Applying requires a Workday
    account per employer, so these jobs are routed to manual review with prepared materials."""
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    jobs, seen = [], set()
    for term in search_terms or [""]:
        for offset in range(0, 100, 20):
            r = request("POST", f"{base}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": term})
            postings = r.json().get("jobPostings", []) if r is not None else []
            for p in postings:
                path = p.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                posted = p.get("postedOn", "")
                if "30+" in posted and max_age_days < 30:
                    continue
                jobs.append(Job(
                    source="workday", external_id=f"{tenant}-{path.rsplit('_', 1)[-1]}", company=company,
                    title=p.get("title", ""), location=p.get("locationsText", ""),
                    url=f"https://{host}/{site}{path}", ats="workday",
                    ats_meta={"host": host, "tenant": tenant, "site": site, "path": path, "needs_detail": True},
                ))
            if len(postings) < 20:
                break
    return jobs


def workday_detail(job: Job) -> None:
    m = job.ats_meta
    d = get_json(f"https://{m['host']}/wday/cxs/{m['tenant']}/{m['site']}{m['path']}")
    info = (d or {}).get("jobPostingInfo") or {}
    job.description = html_to_text(info.get("jobDescription", ""))
    job.posted_at = parse_dt(info.get("startDate")) or job.posted_at
    job.apply_url = info.get("externalUrl") or job.url


def hydrate(job: Job) -> None:
    """Fetch full descriptions for sources whose list endpoint omits them."""
    if not job.ats_meta.get("needs_detail"):
        return
    try:
        if job.ats == "smartrecruiters":
            smartrecruiters_detail(job)
        elif job.ats == "workday":
            workday_detail(job)
    except Exception as e:  # noqa: BLE001
        log.warning("detail fetch failed for %s: %s", job.uid, e)
    job.ats_meta.pop("needs_detail", None)


# ---------------------------------------------------------------- auto-detection


def slug_candidates(name: str) -> list[str]:
    base = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|technologies|labs)\b\.?", "", name.lower())
    words = re.findall(r"[a-z0-9]+", base)
    cands = ["".join(words), "-".join(words), words[0] if words else ""]
    out = []
    for c in cands:
        if c and c not in out:
            out.append(c)
    return out


def detect_board(name: str, careers_url: str = "") -> tuple[str, dict]:
    """Find which ATS a company uses. Tries the careers URL first, then probes public APIs."""
    if careers_url:
        ats, meta = parse_ats_url(careers_url)
        if ats and (meta.get("slug") or ats == "workday"):
            return ats, meta
        # Scan the careers page for an embedded ATS link
        r = request("GET", careers_url, retries=1)
        if r is not None:
            pattern = (r"https?://(?:boards|job-boards)\.greenhouse\.io/[\w-]+|https?://jobs\.lever\.co/[\w-]+"
                       r"|https?://jobs\.ashbyhq\.com/[\w.-]+|https?://[\w-]+\.wd\d+\.myworkdayjobs\.com/[\w/-]+"
                       r"|https?://jobs\.smartrecruiters\.com/[\w-]+|greenhouse\.io/embed/job_board\?for=[\w-]+")
            for m in re.finditer(pattern, r.text):
                link = m.group(0)
                if not link.startswith("http"):
                    link = "https://boards." + link
                ats, meta = parse_ats_url(link)
                if ats:
                    return ats, meta
    for slug in slug_candidates(name):
        if (get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs") or {}).get("jobs"):
            return "greenhouse", {"slug": slug}
        if get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"):
            return "lever", {"slug": slug}
        if (get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}") or {}).get("jobs"):
            return "ashby", {"slug": slug}
        if (get_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1") or {}).get("content"):
            return "smartrecruiters", {"slug": slug}
    return "", {}


def fetch_company(name: str, ats: str, meta: dict, search_terms: list[str], max_age_days: int) -> list[Job]:
    slug = meta.get("slug", "")
    if ats == "greenhouse" and slug:
        return greenhouse(name, slug)
    if ats == "lever" and slug:
        return lever(name, slug)
    if ats == "ashby" and slug:
        return ashby(name, slug)
    if ats == "smartrecruiters" and slug:
        return smartrecruiters(name, slug)
    if ats == "workday":
        return workday(name, meta["host"], meta["tenant"], meta["site"], search_terms, max_age_days)
    return []
