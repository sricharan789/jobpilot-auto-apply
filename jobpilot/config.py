"""Settings come from two places:

* environment variables (secrets and deployment knobs), see .env.example
* profile.yaml (who you are, what you want, which companies to target), see profile.example.yaml
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    data_dir: Path
    profile_path: Path
    anthropic_api_key: str
    claude_model: str
    dashboard_user: str
    dashboard_password: str
    public_url: str
    # scheduling and safety limits
    discover_every_hours: int
    max_scores_per_run: int
    max_applications_per_day: int
    dry_run: bool  # fill forms but never click submit
    # notifications
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    notify_email: str
    ntfy_topic: str
    # optional aggregator keys
    adzuna_app_id: str
    adzuna_app_key: str

    @property
    def db_path(self) -> Path:
        return self.data_dir / "jobpilot.db"

    @property
    def screenshots_dir(self) -> Path:
        p = self.data_dir / "screenshots"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def files_dir(self) -> Path:
        p = self.data_dir / "files"
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings() -> Settings:
    data_dir = Path(_env("DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        data_dir=data_dir,
        profile_path=Path(_env("PROFILE_PATH", str(data_dir / "profile.yaml"))),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        claude_model=_env("CLAUDE_MODEL", "claude-sonnet-5-5"),
        dashboard_user=_env("DASHBOARD_USER", "admin"),
        dashboard_password=_env("DASHBOARD_PASSWORD"),
        public_url=_env("PUBLIC_URL", "http://localhost:8000").rstrip("/"),
        discover_every_hours=_env_int("DISCOVER_EVERY_HOURS", 6),
        max_scores_per_run=_env_int("MAX_SCORES_PER_RUN", 60),
        max_applications_per_day=_env_int("MAX_APPLICATIONS_PER_DAY", 15),
        dry_run=_env_bool("DRY_RUN", True),
        smtp_host=_env("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=_env_int("SMTP_PORT", 587),
        smtp_user=_env("SMTP_USER"),
        smtp_password=_env("SMTP_PASSWORD"),
        notify_email=_env("NOTIFY_EMAIL"),
        ntfy_topic=_env("NTFY_TOPIC"),
        adzuna_app_id=_env("ADZUNA_APP_ID"),
        adzuna_app_key=_env("ADZUNA_APP_KEY"),
    )


# ---------------------------------------------------------------- profile


@dataclass
class Company:
    name: str
    ats: str = ""  # greenhouse | lever | ashby | smartrecruiters | workday (auto-detected if blank)
    slug: str = ""
    careers_url: str = ""


@dataclass
class Profile:
    candidate: dict[str, Any]
    preferences: dict[str, Any]
    search: dict[str, Any]
    companies: list[Company]
    sources: dict[str, bool]
    answers: dict[str, str]
    raw: dict[str, Any] = field(default_factory=dict)

    # convenience accessors -------------------------------------------
    @property
    def titles(self) -> list[str]:
        return [t for t in self.preferences.get("titles", []) if t]

    @property
    def title_exclude(self) -> list[str]:
        return [t for t in self.preferences.get("title_exclude", []) if t]

    @property
    def locations(self) -> list[str]:
        return [loc for loc in self.preferences.get("locations", []) if loc]

    @property
    def remote_ok(self) -> bool:
        return bool(self.preferences.get("remote_ok", True))

    @property
    def min_score(self) -> int:
        return int(self.preferences.get("min_score", 70))

    @property
    def max_age_days(self) -> int:
        return int(self.preferences.get("max_age_days", 21))

    @property
    def resume_path(self) -> str:
        return str(self.candidate.get("resume_path", ""))

    def source_enabled(self, name: str, default: bool = False) -> bool:
        return bool(self.sources.get(name, default))


def parse_profile(data: dict[str, Any]) -> Profile:
    companies = []
    for c in data.get("companies") or []:
        if isinstance(c, str):
            companies.append(Company(name=c))
        elif isinstance(c, dict) and c.get("name"):
            companies.append(
                Company(
                    name=str(c["name"]),
                    ats=str(c.get("ats", "") or "").lower(),
                    slug=str(c.get("slug", "") or ""),
                    careers_url=str(c.get("careers_url", "") or ""),
                )
            )
    return Profile(
        candidate=data.get("candidate") or {},
        preferences=data.get("preferences") or {},
        search=data.get("search") or {},
        companies=companies,
        sources={k: bool(v) for k, v in (data.get("sources") or {}).items()},
        answers={str(k): str(v) for k, v in (data.get("answers") or {}).items()},
        raw=data,
    )


def load_profile(path: Path) -> Profile:
    if not path.exists():
        example = Path(__file__).resolve().parent.parent / "profile.example.yaml"
        raise FileNotFoundError(
            f"No profile at {path}. Copy {example.name} there (or paste it into the dashboard's Settings page)."
        )
    with open(path, encoding="utf-8") as f:
        return parse_profile(yaml.safe_load(f) or {})
