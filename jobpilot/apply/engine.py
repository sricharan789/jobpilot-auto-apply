"""Generic application-form filler for Greenhouse, Lever and Ashby.

Two phases:
  prepare(job)  -> opens the form, fills it, screenshots it, saves every answer. Never submits.
  submit(job)   -> after you approve, re-fills using the saved (possibly edited) answers and submits.

It stops and flags the job for you on CAPTCHAs, validation errors, or required fields it
could not fill. It never solves CAPTCHAs, creates accounts, or logs in anywhere.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..config import Profile, Settings
from ..models import norm
from ..scoring import Scorer
from .coverletter import write_cover_letter_pdf

log = logging.getLogger(__name__)
FIELDS_JS = (Path(__file__).parent / "fields.js").read_text()

CONFIRM_RE = re.compile(
    r"thank(s| you) for (applying|your (application|interest))|application (has been |was )?(received|submitted)"
    r"|we('|’)ve received your application|successfully submitted|application complete", re.I)
SUBMIT_RE = re.compile(r"^\s*(submit( your)?( application)?|send application|apply( now)?)\s*$", re.I)


@dataclass
class Result:
    ok: bool
    stage: str  # prepared | submitted | attention
    message: str = ""
    answers: dict[str, dict[str, Any]] = field(default_factory=dict)
    screenshot: str = ""
    unsure: list[str] = field(default_factory=list)


def field_key(f: dict[str, Any], seen: dict[str, int]) -> str:
    """Stable key for a field across page loads: its normalized label (+ counter for repeats)."""
    base = (norm(f.get("label", "")) or f.get("kind", "field"))[:90]
    seen[base] = seen.get(base, 0) + 1
    return base if seen[base] == 1 else f"{base} #{seen[base]}"


def best_option(value: str, options: list[str]) -> str | None:
    if not options:
        return None
    v = norm(value)
    for o in options:
        if norm(o) == v:
            return o
    for o in options:
        if v and (v in norm(o) or norm(o) in v):
            return o
    scored = max(options, key=lambda o: SequenceMatcher(None, v, norm(o)).ratio())
    return scored if SequenceMatcher(None, v, norm(scored)).ratio() > 0.55 else None


class Filler:
    def __init__(self, settings: Settings, profile: Profile, scorer: Scorer | None):
        self.settings, self.profile, self.scorer = settings, profile, scorer
        c = profile.candidate
        full = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
        # (label pattern, value). First match wins; order matters.
        self.standard: list[tuple[re.Pattern, str]] = [
            (re.compile(r"\bfirst name|given name|^first$", re.I), c.get("first_name", "")),
            (re.compile(r"\blast name|surname|family name|^last$", re.I), c.get("last_name", "")),
            (re.compile(r"preferred (first )?name", re.I), c.get("preferred_name") or c.get("first_name", "")),
            (re.compile(r"^(full )?name$|^your name|legal name", re.I), full),
            (re.compile(r"e-?mail", re.I), c.get("email", "")),
            (re.compile(r"phone|mobile", re.I), str(c.get("phone", ""))),
            (re.compile(r"linkedin", re.I), c.get("linkedin", "")),
            (re.compile(r"github", re.I), c.get("github", "")),
            (re.compile(r"portfolio|personal (web)?site|^website", re.I), c.get("website", "")),
            (re.compile(r"^(current )?location$|^city$|where are you (located|based)", re.I), c.get("location", "")),
            (re.compile(r"current (company|employer)|^company$|^org", re.I), c.get("current_company", "")),
        ]

    # ------------------------------------------------------------ answers
    def _standard_value(self, f: dict[str, Any]) -> str | None:
        label = f.get("label", "")
        if f["kind"] not in {"file", "radio", "checkbox", "select", "combobox"}:
            for pat, val in self.standard:
                if val and pat.search(label):
                    return str(val)
        for q, a in self.profile.answers.items():  # your canned answers from profile.yaml
            if norm(q) and norm(q) in norm(label):
                return a
        return None

    def decide(self, job: dict[str, Any], fields: list[dict[str, Any]],
               saved: dict[str, dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
        answers: dict[str, dict[str, Any]] = {}
        ask: list[dict[str, Any]] = []
        for f in fields:
            k = f["key"]
            if f["kind"] == "file":
                lab = f["label"].lower()
                if "autofill" in lab or "parse" in lab:
                    continue
                if "cover" in lab:
                    answers[k] = {"label": f["label"], "value": "<cover letter PDF>", "source": "generated", "confident": True}
                elif f.get("required") or re.search(r"resume|cv\b", lab) or not lab:
                    answers[k] = {"label": f["label"], "value": "<resume>", "source": "profile", "confident": True}
                continue
            if saved and k in saved:
                answers[k] = {**saved[k], "label": f["label"]}
                continue
            if f["kind"] == "textarea" and "cover letter" in f["label"].lower() and job.get("cover_letter"):
                answers[k] = {"label": f["label"], "value": job["cover_letter"], "source": "cover letter", "confident": True}
                continue
            v = self._standard_value(f)
            if v is not None:
                answers[k] = {"label": f["label"], "value": v, "source": "profile", "confident": True}
            else:
                ask.append({"key": k, "label": f["label"], "kind": f["kind"], "required": f.get("required", False),
                            **({"options": f["options"]} if f.get("options") else {})})
        if ask and self.scorer:
            for k, a in self.scorer.answer_fields(job, ask).items():
                lab = next((x["label"] for x in ask if x["key"] == k), k)
                answers[k] = {"label": lab, "value": a["value"], "source": "claude", "confident": a["confident"]}
        return answers

    # ------------------------------------------------------------ browser
    def _fields(self, page) -> list[dict[str, Any]]:
        fields = page.evaluate(FIELDS_JS)
        seen: dict[str, int] = {}
        for f in fields:
            f["key"] = field_key(f, seen)
            if f["kind"] == "combobox":
                f["options"] = self._combobox_options(page, f["ref"])
            if f.get("single"):
                f["options"] = ["Yes", "No"]
        return fields

    @staticmethod
    def _combobox_options(page, ref: str) -> list[str]:
        try:
            el = page.locator(f'[data-jp="{ref}"]')
            el.click(timeout=3000)
            page.wait_for_timeout(400)
            opts = [t.strip() for t in page.locator("[role=option]").all_inner_texts() if t.strip()][:60]
            el.press("Escape")
            return opts
        except Exception:  # noqa: BLE001
            return []

    def _fill_one(self, page, f: dict[str, Any], value: str, job: dict[str, Any]) -> bool:
        kind = f["kind"]
        try:
            if kind == "file":
                path = self.profile.resume_path
                if value == "<cover letter PDF>":
                    path = str(write_cover_letter_pdf(self.settings.files_dir, job, self.profile))
                page.locator(f'[data-jp="{f["ref"]}"]').set_input_files(path, timeout=8000)
                page.wait_for_timeout(1200)
                return True
            if value in ("", None):
                return True
            if kind in {"radio", "checkbox"}:
                if f.get("single"):
                    want = norm(value) in {"yes", "true", "checked", "agree", "i agree"}
                    loc = page.locator(f'[data-jp-opt="{f["optRefs"][0]}"]')
                    if want != loc.is_checked():
                        loc.check(force=True) if want else loc.uncheck(force=True)
                    return True
                picks = [v.strip() for v in value.split(";")] if f.get("multiple") else [value]
                for p in picks:
                    opt = best_option(p, f["options"])
                    if opt is None:
                        return False
                    page.locator(f'[data-jp-opt="{f["optRefs"][f["options"].index(opt)]}"]').check(force=True)
                return True
            loc = page.locator(f'[data-jp="{f["ref"]}"]')
            if kind == "select":
                opt = best_option(value, f.get("options", []))
                if opt is None:
                    return False
                loc.select_option(label=opt)
                return True
            if kind == "combobox":
                opt = best_option(value, f.get("options", [])) or value
                loc.click()
                loc.fill(opt)
                page.wait_for_timeout(600)
                option = page.locator("[role=option]", has_text=opt).first
                if option.count():
                    option.click()
                else:
                    loc.press("Enter")
                return True
            loc.fill(str(value))
            return True
        except Exception as e:  # noqa: BLE001
            log.info("fill failed for %s: %s", f.get("label"), e)
            return False

    @staticmethod
    def _visible_captcha(page) -> bool:
        return bool(page.evaluate("""() => {
            const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 30 && r.height > 30 &&
                getComputedStyle(el).visibility !== 'hidden'; };
            return Array.from(document.querySelectorAll('iframe')).some((f) => {
                const s = f.src || '';
                const challenge = /recaptcha\\/api2\\/(anchor|bframe)|hcaptcha\\.com|challenges\\.cloudflare\\.com/.test(s);
                return challenge && vis(f) && !f.closest('.grecaptcha-badge');
            });
        }"""))

    @staticmethod
    def _errors(page) -> list[str]:
        return page.evaluate("""() => Array.from(document.querySelectorAll(
            '[role=alert], .error, .field-error, [class*="error"], [aria-invalid=true]'))
            .filter((e) => e.offsetParent !== null).map((e) => (e.innerText || e.getAttribute('aria-label') || '').trim())
            .filter((t) => t && t.length < 200).slice(0, 8)""")

    def _shot(self, page, job: dict[str, Any], stage: str) -> str:
        name = re.sub(r"[^\w-]+", "_", job["uid"])[:80] + f"-{stage}.png"
        path = self.settings.screenshots_dir / name
        try:
            page.screenshot(path=str(path), full_page=True)
        except Exception:  # noqa: BLE001
            return ""
        return name

    def _open(self, pw, job: dict[str, Any]):
        browser = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 1800}, locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(15000)
        url = job.get("apply_url") or job["url"]
        if job.get("ats") == "ashby" and "/application" not in url:
            url = url.rstrip("/") + "/application"
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:  # noqa: BLE001
            pass
        if job.get("ats") == "lever" and "/apply" not in page.url:
            btn = page.get_by_role("link", name=re.compile("apply", re.I)).first
            if btn.count():
                btn.click()
                page.wait_for_load_state("domcontentloaded")
        return browser, page

    # ------------------------------------------------------------ public
    def run(self, job: dict[str, Any], submit: bool, saved: dict[str, dict[str, Any]] | None = None) -> Result:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser, page = self._open(pw, job)
            try:
                return self._run(page, job, submit, saved)
            except Exception as e:  # noqa: BLE001
                log.exception("apply failed for %s", job["uid"])
                return Result(False, "attention", f"Automation error: {e}", screenshot=self._shot(page, job, "error"))
            finally:
                browser.close()

    def _run(self, page, job, submit, saved) -> Result:
        fields = self._fields(page)
        if not fields:
            return Result(False, "attention", "No application form found on the page.",
                          screenshot=self._shot(page, job, "noform"))

        # Upload resume first: some ATSes autofill fields from it, which we then overwrite.
        answers = self.decide(job, fields, saved)
        order = sorted(fields, key=lambda f: 0 if f["kind"] == "file" else 1)
        problems = []
        # ATS check: if a cover letter PDF will be attached, verify its text layer extracts cleanly first.
        if job.get("cover_letter") and any(a["value"] == "<cover letter PDF>" for a in answers.values()):
            from .coverletter import verify_pdf_text, write_cover_letter_pdf
            c = self.profile.candidate
            must = [x for x in (c.get("first_name", ""), c.get("email", "")) if x]
            pdf = write_cover_letter_pdf(self.settings.files_dir, job, self.profile)
            ok, note = verify_pdf_text(pdf, must)
            if not ok:
                problems.append("Cover letter ATS check: " + note)

        failed = []
        for f in order:
            a = answers.get(f["key"])
            if a is None:
                continue
            if not self._fill_one(page, f, a["value"], job):
                failed.append(f["label"] or f["key"])

        unsure = [a["label"] for a in answers.values() if not a.get("confident", True)]
        missing = [f["label"] for f in fields if f.get("required") and not answers.get(f["key"], {}).get("value")
                   and f["kind"] != "file"]
        if failed:
            problems.append("Couldn't fill: " + "; ".join(failed[:6]))
        if missing:
            problems.append("Required with no answer: " + "; ".join(missing[:6]))

        if not submit:
            shot = self._shot(page, job, "prepared")
            msg = "Form pre-filled. Review the answers and screenshot, then approve."
            if problems:
                msg = " ".join(problems) + ". Edit the answers below before approving, or apply manually."
            return Result(True, "prepared", msg, answers, shot, unsure)

        if problems:
            return Result(False, "attention", " ".join(problems), answers, self._shot(page, job, "blocked"), unsure)
        if self._visible_captcha(page):
            return Result(False, "attention", "CAPTCHA on the form. Open the link and submit it yourself "
                          "(answers below are ready to copy).", answers, self._shot(page, job, "captcha"), unsure)
        if self.settings.dry_run:
            return Result(False, "attention", "DRY_RUN is on, so nothing was submitted. Set DRY_RUN=false to go live.",
                          answers, self._shot(page, job, "dryrun"), unsure)

        btn = page.get_by_role("button", name=SUBMIT_RE).last
        if not btn.count():
            btn = page.locator("button[type=submit], input[type=submit]").last
        btn.click()
        for _ in range(20):
            page.wait_for_timeout(1000)
            text = page.inner_text("body")[:20000]
            if CONFIRM_RE.search(text) or re.search(r"confirm|thank", page.url, re.I):
                return Result(True, "submitted", "Application submitted.", answers, self._shot(page, job, "submitted"))
            if self._visible_captcha(page):
                return Result(False, "attention", "A CAPTCHA appeared on submit. Open the link to finish it yourself.",
                              answers, self._shot(page, job, "captcha"), unsure)
        errs = self._errors(page)
        msg = "No confirmation after submitting." + (f" Page shows: {'; '.join(errs)}" if errs else "")
        return Result(False, "attention", msg, answers, self._shot(page, job, "unconfirmed"), unsure)
