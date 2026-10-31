"""Cheap, free pre-filter so Claude only scores plausible jobs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import Profile
from .models import Job, norm

REMOTE_WORDS = ("remote", "anywhere", "distributed", "work from home")


def title_matches(title: str, wanted: list[str], exclude: list[str]) -> bool:
    t = f" {norm(title)} "
    if any(f" {norm(x)} " in t for x in exclude):
        return False
    if not wanted:
        return True
    # A wanted title matches when every word in it appears in the job title,
    # so "software engineer" matches "Senior Software Engineer, Payments".
    return any(all(f" {w} " in t for w in norm(w_title).split()) for w_title in wanted)


def location_matches(job: Job, locations: list[str], remote_ok: bool) -> bool:
    loc = (job.location or "").lower()
    if remote_ok and (job.remote or any(w in loc for w in REMOTE_WORDS)):
        return True
    if not locations:
        return True
    if not loc:  # unknown location: let Claude decide
        return True
    return any(part.strip().lower() in loc for want in locations for part in want.split("|") if part.strip())


def is_recent(job: Job, max_age_days: int) -> bool:
    if job.posted_at is None:
        return True
    return job.posted_at >= datetime.now(timezone.utc) - timedelta(days=max_age_days)


def prefilter(job: Job, profile: Profile) -> tuple[bool, str]:
    if not title_matches(job.title, profile.titles, profile.title_exclude):
        return False, "title"
    if not location_matches(job, profile.locations, profile.remote_ok):
        return False, "location"
    if not is_recent(job, profile.max_age_days):
        return False, "too old"
    return True, ""
