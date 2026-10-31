from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from . import notify, sources
from .config import Profile, Settings, load_profile
from .db import DB, now
from .filters import prefilter
from .models import (APPLIED, APPROVED, ATTENTION, AUTO_ATS, LOW_SCORE, REJECTED, REVIEW, TO_PREPARE,
                     TO_SCORE, Job, norm)
from .resume import resume_text
from .scoring import Scorer

log = logging.getLogger(__name__)
BOARD_REFRESH_DAYS = 7
PREPARE_PER_RUN = 10


class Pipeline:
    def __init__(self, settings: Settings, db: DB):
        self.settings, self.db = settings, db
        self.cycle_lock = threading.Lock()
        self.apply_lock = threading.Lock()
        self.last_status = "idle"

    def profile(self) -> Profile:
        return load_profile(self.settings.profile_path)  # re-read each run so dashboard edits apply

    def _scorer(self, profile: Profile) -> Scorer:
        return Scorer(self.settings, profile, resume_text(profile.resume_path))

    # ------------------------------------------------------------ discover
    def _board(self, name: str, ats: str, slug: str, careers_url: str) -> tuple[str, dict]:
        if ats and slug:
            return ats, {"slug": slug}
        cached = self.db.get_company(name)
        fresh = cached and cached["checked_at"] > (datetime.now(timezone.utc) - timedelta(days=BOARD_REFRESH_DAYS)).isoformat()
        if fresh and cached["ats"]:
            return cached["ats"], cached["meta"]
        ats, meta = sources.detect_board(name, careers_url)
        self.db.set_company(name, ats, meta)
        if not ats:
            self.db.log("warn", f"Couldn't find a jobs board for {name}. Add its careers_url in your profile.")
        return ats, meta

    def _save(self, job: Job, profile: Profile, targets: set[str]) -> bool:
        if norm(job.company) in targets:
            job.ats_meta["target"] = True
        ok, reason = prefilter(job, profile)
        if ok:
            sources.hydrate(job)
        if not self.db.insert_job(job):
            return False
        self.db.update_job(job.uid, status=TO_SCORE if ok else REJECTED, error=reason or None)
        return ok

    def discover(self) -> int:
        profile = self.profile()
        targets = {norm(c.name) for c in profile.companies}
        terms = profile.titles[:4]
        found = relevant = 0

        def take(jobs: list[Job]) -> None:
            nonlocal found, relevant
            for j in jobs:
                found += 1
                relevant += self._save(j, profile, targets)

        for c in profile.companies:
            try:
                ats, meta = self._board(c.name, c.ats, c.slug, c.careers_url)
                if ats:
                    take(sources.fetch_company(c.name, ats, meta, terms, profile.max_age_days))
            except Exception as e:  # noqa: BLE001
                self.db.log("error", f"{c.name}: {e}")

        keywords = profile.search.get("keywords") or profile.titles[:3]
        where = profile.search.get("location", "United States")
        try:
            if profile.source_enabled("adzuna", True):
                take(sources.adzuna(self.settings.adzuna_app_id, self.settings.adzuna_app_key, keywords, where,
                                    profile.max_age_days, profile.search.get("country", "us")))
            if profile.source_enabled("remoteok"):
                take(sources.remoteok(keywords))
            if profile.source_enabled("linkedin"):
                take(sources.linkedin(keywords, where, profile.max_age_days,
                                      is_relevant=lambda j: prefilter(j, profile)[0]))
        except Exception as e:  # noqa: BLE001
            self.db.log("error", f"aggregator: {e}")
        self.db.log("discover", f"Scanned {found} listings; {relevant} new relevant job(s) queued for scoring.")
        return relevant

    # ------------------------------------------------------------ score
    def score(self) -> list[dict[str, Any]]:
        profile = self.profile()
        pending = self.db.jobs_by_status(TO_SCORE, order="discovered_at DESC", limit=1000)
        pending.sort(key=lambda j: not (j.get("ats_meta") or {}).get("target"))  # target companies first
        pending = pending[: self.settings.max_scores_per_run]
        if not pending:
            return []
        scorer = self._scorer(profile)
        good = []
        for job in pending:
            try:
                a = scorer.assess(job)
            except Exception as e:  # noqa: BLE001
                self.db.log("error", f"scoring failed: {e}", job["uid"])
                continue
            passes = a["score"] >= profile.min_score and not a.get("dealbreaker")
            status = LOW_SCORE if not passes else (TO_PREPARE if job["ats"] in AUTO_ATS else REVIEW)
            self.db.update_job(job["uid"], status=status, score=a["score"], assessment=a,
                               cover_letter=a.get("cover_letter", ""))
            if passes:
                good.append(self.db.get_job(job["uid"]))
        self.db.log("score", f"Scored {len(pending)} job(s); {len(good)} above your threshold.")
        return good

    # ------------------------------------------------------------ prepare / apply
    def _filler(self, profile: Profile):
        from .apply import Filler
        return Filler(self.settings, profile, self._scorer(profile))

    def prepare(self, uid: str | None = None) -> int:
        profile = self.profile()
        jobs = [self.db.get_job(uid)] if uid else self.db.jobs_by_status(TO_PREPARE, limit=PREPARE_PER_RUN)
        if not jobs:
            return 0
        filler = self._filler(profile)
        for job in jobs:
            r = filler.run(job, submit=False)
            self.db.update_job(job["uid"], status=REVIEW, answers=r.answers or None,
                               screenshot=r.screenshot or None, error=None if r.ok and "Review" in r.message else r.message)
            self.db.log("prepare", r.message, job["uid"])
        return len(jobs)

    def apply_approved(self) -> int:
        if not self.apply_lock.acquire(blocking=False):
            return 0
        try:
            since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            budget = self.settings.max_applications_per_day - self.db.applied_since(since)
            jobs = self.db.jobs_by_status(APPROVED, order="updated_at ASC", limit=max(0, budget))
            if not jobs:
                return 0
            profile = self.profile()
            filler = self._filler(profile)
            done = 0
            for job in jobs:
                r = filler.run(job, submit=True, saved=job.get("answers") or None)
                if r.stage == "submitted":
                    done += 1
                    self.db.update_job(job["uid"], status=APPLIED, applied_at=now(), screenshot=r.screenshot, error=None)
                    notify.push(self.settings, "Applied", f"{job['title']} at {job['company']}",
                                f"{self.settings.public_url}/job/{job['uid']}")
                else:
                    self.db.update_job(job["uid"], status=ATTENTION, error=r.message,
                                       screenshot=r.screenshot or job.get("screenshot"), answers=r.answers or job.get("answers"))
                    notify.push(self.settings, "Needs you", f"{job['title']} at {job['company']}: {r.message[:120]}",
                                f"{self.settings.public_url}/job/{job['uid']}")
                self.db.log("apply", r.message, job["uid"])
            return done
        finally:
            self.apply_lock.release()

    # ------------------------------------------------------------ full cycle
    def run_cycle(self) -> None:
        if not self.cycle_lock.acquire(blocking=False):
            return
        try:
            self.last_status = "discovering"
            self.discover()
            self.last_status = "scoring"
            good = self.score()
            self.last_status = "preparing applications"
            self.prepare()
            fresh = [self.db.get_job(j["uid"]) for j in good]
            notify.digest(self.settings, sorted([j for j in fresh if j and j["status"] == REVIEW],
                                                key=lambda j: -(j.get("score") or 0)))
        except Exception as e:  # noqa: BLE001
            log.exception("cycle failed")
            self.db.log("error", f"Cycle failed: {e}")
        finally:
            self.last_status = f"idle (last run {now()})"
            self.cycle_lock.release()
