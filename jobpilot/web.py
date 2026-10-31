from __future__ import annotations

import logging
import secrets
import threading
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .apply.coverletter import write_cover_letter_pdf
from .config import load_settings, parse_profile
from .db import DB
from .models import (APPLIED, APPROVED, ATTENTION, LOW_SCORE, REJECTED, REVIEW, SKIPPED, TO_PREPARE, TO_SCORE)
from .pipeline import Pipeline

log = logging.getLogger("jobpilot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

settings = load_settings()
db = DB(settings.db_path)
pipeline = Pipeline(settings, db)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()

if not settings.dashboard_password:
    settings.dashboard_password = secrets.token_urlsafe(12)
    log.warning("DASHBOARD_PASSWORD not set. Temporary password for this run: %s", settings.dashboard_password)

TABS = {
    "review": ("To review", [REVIEW]),
    "queue": ("Approved", [APPROVED, TO_PREPARE, TO_SCORE]),
    "attention": ("Needs you", [ATTENTION]),
    "applied": ("Applied", [APPLIED]),
    "low": ("Low fit", [LOW_SCORE]),
    "skipped": ("Skipped", [SKIPPED]),
    "rejected": ("Filtered out", [REJECTED]),
}


def auth(creds: HTTPBasicCredentials = Depends(security)) -> str:
    ok_user = secrets.compare_digest(creds.username.encode(), settings.dashboard_user.encode())
    ok_pass = secrets.compare_digest(creds.password.encode(), settings.dashboard_password.encode())
    if not (ok_user and ok_pass):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})
    return creds.username


def background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(_: FastAPI):
    from datetime import datetime, timedelta, timezone
    scheduler.add_job(pipeline.run_cycle, "interval", hours=settings.discover_every_hours, id="cycle",
                      next_run_time=datetime.now(timezone.utc) + timedelta(minutes=1), max_instances=1, coalesce=True)
    scheduler.add_job(pipeline.apply_approved, "interval", minutes=3, id="apply", max_instances=1, coalesce=True)
    scheduler.start()
    log.info("JobPilot running. Discovery every %sh. DRY_RUN=%s", settings.discover_every_hours, settings.dry_run)
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, title="JobPilot")


def profile_or_none():
    try:
        return pipeline.profile()
    except Exception:  # noqa: BLE001
        return None


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, tab: str = "review", user: str = Depends(auth)):
    tab = tab if tab in TABS else "review"
    counts = db.counts()
    tab_counts = {k: sum(counts.get(s, 0) for s in v[1]) for k, v in TABS.items()}
    order = "applied_at DESC" if tab == "applied" else "score DESC, discovered_at DESC"
    jobs = db.jobs_by_status(*TABS[tab][1], order=order, limit=300)
    return templates.TemplateResponse(request, "index.html", {
        "tabs": TABS, "tab": tab, "tab_counts": tab_counts, "jobs": jobs, "status": pipeline.last_status,
        "settings": settings, "events": db.recent_events(8), "profile_ok": profile_or_none() is not None})


@app.get("/job/{uid:path}", response_class=HTMLResponse)
def job_page(request: Request, uid: str, user: str = Depends(auth)):
    job = db.get_job(uid)
    if not job:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "job.html", {"job": job, "settings": settings})


@app.post("/act")
async def act(request: Request, user: str = Depends(auth)):
    form = await request.form()
    uid, action = str(form.get("uid", "")), str(form.get("action", ""))
    job = db.get_job(uid)
    if not job:
        raise HTTPException(404)
    if action in {"save", "approve"}:
        answers = dict(job.get("answers") or {})
        for k, v in form.items():
            if k.startswith("ans::") and k[5:] in answers:
                if answers[k[5:]].get("value") != v:
                    answers[k[5:]] = {**answers[k[5:]], "value": str(v), "source": "you", "confident": True}
        db.update_job(uid, cover_letter=str(form.get("cover_letter", job.get("cover_letter") or "")), answers=answers)
    if action == "approve":
        db.update_job(uid, status=APPROVED, error=None)
        db.log("approve", "Approved for submission", uid)
        background(pipeline.apply_approved)
        return RedirectResponse("/?tab=review", 303)
    if action == "skip":
        db.update_job(uid, status=SKIPPED)
        return RedirectResponse("/?tab=review", 303)
    if action == "applied":
        from .db import now
        db.update_job(uid, status=APPLIED, applied_at=now(), error=None)
        db.log("apply", "Marked applied manually", uid)
        return RedirectResponse("/?tab=review", 303)
    if action == "prepare":
        db.update_job(uid, status=TO_PREPARE)
        background(pipeline.prepare, uid)
    if action == "review":
        db.update_job(uid, status=REVIEW)
    return RedirectResponse(f"/job/{uid}", 303)


@app.post("/run")
def run_now(user: str = Depends(auth)):
    background(pipeline.run_cycle)
    return RedirectResponse("/", 303)


@app.get("/shots/{name}")
def shot(name: str, user: str = Depends(auth)):
    path = (settings.screenshots_dir / name).resolve()
    if path.parent != settings.screenshots_dir.resolve() or not path.exists():
        raise HTTPException(404)
    return FileResponse(path)


@app.get("/cover/{uid:path}")
def cover(uid: str, user: str = Depends(auth)):
    job, profile = db.get_job(uid), profile_or_none()
    if not job or not profile:
        raise HTTPException(404)
    return FileResponse(write_cover_letter_pdf(settings.files_dir, job, profile), media_type="application/pdf")


# ---------------------------------------------------------------- settings


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, msg: str = "", user: str = Depends(auth)):
    p = settings.profile_path
    example = Path(__file__).resolve().parent.parent / "profile.example.yaml"
    text = p.read_text() if p.exists() else (example.read_text() if example.exists() else "")
    prof = profile_or_none()
    resume = Path(prof.resume_path) if prof and prof.resume_path else None
    return templates.TemplateResponse(request, "settings.html", {
        "profile_text": text, "msg": msg, "settings": settings, "exists": p.exists(),
        "resume": resume.name if resume and resume.exists() else None,
        "companies": [(c.name, db.get_company(c.name)) for c in (prof.companies if prof else [])]})


@app.post("/settings/profile")
def save_profile(profile_yaml: str = Form(...), user: str = Depends(auth)):
    try:
        parse_profile(yaml.safe_load(profile_yaml) or {})
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(f"/settings?msg=Not saved, YAML error: {str(e)[:200]}", 303)
    settings.profile_path.write_text(profile_yaml)
    return RedirectResponse("/settings?msg=Profile saved.", 303)


@app.post("/settings/resume")
async def upload_resume(file: UploadFile = File(...), user: str = Depends(auth)):
    suffix = Path(file.filename or "resume.pdf").suffix.lower()
    if suffix not in {".pdf", ".docx", ".txt"}:
        return RedirectResponse("/settings?msg=Upload a PDF, DOCX or TXT resume.", 303)
    dest = settings.data_dir / f"resume{suffix}"
    dest.write_bytes(await file.read())
    if settings.profile_path.exists():
        data = yaml.safe_load(settings.profile_path.read_text()) or {}
        data.setdefault("candidate", {})["resume_path"] = str(dest)
        settings.profile_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return RedirectResponse(f"/settings?msg=Resume saved to {dest}.", 303)
