from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Job lifecycle
NEW = "new"                # discovered, not yet filtered
REJECTED = "rejected"      # failed the cheap pre-filter (title/location/age)
TO_SCORE = "to_score"      # passed pre-filter, waiting for Claude
LOW_SCORE = "low_score"    # scored below your threshold
TO_PREPARE = "to_prepare"  # good match on a supported ATS; form will be pre-filled
REVIEW = "review"          # waiting for you: approve, skip, or apply manually
APPROVED = "approved"      # you approved; worker will submit
APPLIED = "applied"
SKIPPED = "skipped"
ATTENTION = "attention"    # captcha, form error, or unsupported field; needs a human

AUTO_ATS = {"greenhouse", "lever", "ashby"}  # ATSes the form filler can submit to


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Job:
    source: str
    external_id: str
    company: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    apply_url: str = ""
    posted_at: datetime | None = None
    ats: str = ""
    ats_meta: dict[str, Any] = field(default_factory=dict)
    salary: str = ""
    remote: bool | None = None

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.external_id}"

    @property
    def dedupe_key(self) -> str:
        # Same job is often listed on the company ATS, LinkedIn and an aggregator.
        loc = norm(self.location).split(" ")[0] if self.location else ""
        return f"{norm(self.company)}|{norm(self.title)}|{loc}"

    @property
    def can_autofill(self) -> bool:
        return self.ats in AUTO_ATS
