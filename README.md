# JobPilot

**An automated job-application assistant you run yourself.** It finds jobs at the companies you care about (and across the wider market), scores each one against your resume with Claude, writes a tailored cover letter, pre-fills the application form, and submits it **only after you approve** from a phone-friendly dashboard. It can run in the cloud so it keeps working while your computer is off, or on your own laptop.

> **Heads up — read the [Safety & honest limits](#safety--honest-limits) section before you turn off dry-run.** This tool fills out and (once you enable it) submits real job applications on your behalf. It ships in a safe mode where it submits nothing until you explicitly turn that on.

## Quick start (5 minutes, on your own computer)

You need: **Python 3.10+** and an **Anthropic API key** (from [console.anthropic.com](https://console.anthropic.com)).

**macOS / Linux**
```bash
./setup.sh                       # installs everything, creates .env + a sample profile
# then edit .env  (add your API key + a dashboard password)
set -a; . ./.env; set +a         # load the settings
python3 -m jobpilot doctor       # checks your setup is ready
python3 -m jobpilot serve        # dashboard at http://localhost:8000
```

**Windows** (PowerShell)
```powershell
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env           # then edit .env
copy profile.example.yaml data\profile.yaml
python -m jobpilot doctor
python -m jobpilot serve
```

Open **http://localhost:8000** (username `admin`, the password you set), go to **Settings**, upload your resume and fill in your profile, then press **Run discovery now**. Matches show up in your review queue. Nothing is submitted until you flip `DRY_RUN=false` in `.env`.

To keep it running 24/7 while your computer is off, deploy it to the cloud — see [Deploy](#deploy-flyio-57month--claude-api-usage) below.

## How it works

```
every 6h  ┌─ Discover ──────────────────────────────────────────────┐
          │ Your target companies → auto-detects their ATS          │
          │   (Greenhouse, Lever, Ashby, SmartRecruiters, Workday)  │
          │ + Adzuna, RemoteOK, LinkedIn (read-only)                │
          └──────────────┬──────────────────────────────────────────┘
                         ▼  free pre-filter: title, location, age, duplicates
          ┌─ Score (Claude) ─ fit 0–100, why, gaps, red flags, cover letter ┐
                         ▼  score ≥ min_score
          ┌─ Prepare ─ opens the real form in headless Chrome, fills it,   ┐
          │            screenshots it. Never submits.                       │
                         ▼  email digest + phone push
          ┌─ You review ─ edit any answer or the cover letter → Approve     ┐
                         ▼
          ┌─ Submit ─ re-fills with your approved answers, submits,        ┐
          │           confirms. CAPTCHA or error → "Needs you" + push.      │
```

**Auto-apply works on:** Greenhouse, Lever, Ashby (most startups and many large tech companies).
**Prepared for you to apply manually:** Workday, SmartRecruiters, LinkedIn Easy Apply, other sites. You still get the score, tailored cover letter PDF, and a one-tap link.

### Fit scoring (what reaches your queue)

Each job Claude scores runs **two hard gates before anything else**, because a job you can't hold is worthless however well your skills match:

- **Eligibility gate** — reads the posting's work-rights section against your profile's `work_authorization` / `requires_sponsorship`. A posting that requires citizenship, a security clearance, or explicitly won't sponsor (when you need it) is scored **0** and never queued. A posting that's *silent* on work rights is marked "unverified" (check the employer's page), never auto-failed.
- **Language gate** — a role that requires a language you don't list is failed; one that asks for a higher level than you declared is flagged for your judgment.

Jobs that pass are scored on five dimensions (technical skills, experience match, culture fit, career alignment, location) shown on the job page, with an honest list of gaps — never keyword-stuffed.

### Cover letters (drafter–reviewer)

The first pass drafts a forward-looking letter grounded only in your resume. A **second Claude pass, as a fresh reviewer, critiques it** — missed keywords you actually support, generic or cliché lines, unverifiable company claims, em-dashes — and rewrites it if needed. The reviewer's notes are shown on the job page. Before the PDF is attached, its **text layer is verified** the way an ATS parser reads it (your name and email must survive as literal text), and a garbled or near-empty letter flags the job instead of being sent.

*The scoring rubric, gates, drafter–reviewer pattern, and ATS-text idea are adapted from the MIT-licensed [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) framework.*

## Safety & honest limits

**Please read this before sharing or before turning off dry-run.**

- **It submits nothing until you say so.** `DRY_RUN=true` is the default: the app fills every form and screenshots it, but never clicks submit. Run it this way for a few days, check the screenshots, and only set `DRY_RUN=false` once you trust what it's doing.
- **You approve every application.** Even with dry-run off, each job waits in your review queue. You see the fit score, the cover letter, and every form answer, and can edit any of them before approving. It does not fire off applications on its own.
- **It never invents experience.** Cover letters and answers are grounded in your actual resume. Gaps are shown honestly, never papered over. A bad application reflects on *you*, so the tool is deliberately conservative — but you are still responsible for what you submit. Review before approving.
- **Auto-submit covers Greenhouse, Lever, and Ashby only** (most startups + many large tech companies). Workday, SmartRecruiters, LinkedIn Easy Apply, and other portals are *prepared* for you — score, cover letter, a one-tap link — but you submit those yourself.
- **It will not apply on LinkedIn or Indeed.** Both prohibit automated applying and ban accounts for it. LinkedIn is used for discovery only, without logging in, at low volume; when a LinkedIn listing points to a company's own Greenhouse/Lever/Ashby page, JobPilot follows through there. Indeed is not scraped at all. **Each person runs their own copy with their own data** — don't point it at someone else's accounts.
- **It will not solve CAPTCHAs, create accounts, or log in anywhere.** Those jobs land in a **Needs you** queue with the answers ready for you to copy and finish by hand.
- **Costs:** Claude API usage (a few cents per job scored) plus, if you deploy it, ~$5–7/month for cloud hosting. Running locally is free apart from the API usage.
- **Your data stays yours.** Your resume and profile live in your own `data/` folder (or your own cloud instance) and are sent only to the Anthropic API for scoring and to the employer's own application form. Don't commit your `.env` or `data/` folder to a public repo — both are git-ignored by default.

### What it does NOT include
- No CV/resume generation — it uses the resume you give it as-is (only the cover letter is tailored per job).
- No interview tracking, outcome/offer pipeline, or CRM.
- It's single-user: one person, one profile per instance.

## Deploy (Fly.io, ~$5–7/month + Claude API usage)

1. Install the Fly CLI and sign up: https://fly.io/docs/flyctl/install/
2. In this folder:
   ```bash
   # edit fly.toml: set a unique `app` name
   fly launch --copy-config --no-deploy
   fly volumes create jobpilot_data --size 1 --region sjc
   fly secrets set ANTHROPIC_API_KEY=sk-ant-... DASHBOARD_PASSWORD='long-random-password' \
       PUBLIC_URL=https://<your-app>.fly.dev
   # optional: email digest + phone push + Adzuna
   fly secrets set SMTP_USER=you@gmail.com SMTP_PASSWORD='gmail-app-password' NOTIFY_EMAIL=you@gmail.com \
       NTFY_TOPIC=jobpilot-<random> ADZUNA_APP_ID=... ADZUNA_APP_KEY=...
   fly deploy
   ```
3. Open `https://<your-app>.fly.dev/settings` (user `admin`, your password):
   upload your resume, paste and edit your profile (start from `profile.example.yaml`), save.
4. Press **Run discovery now** on the home page, or wait for the schedule.
5. When the results look right: `fly secrets set DRY_RUN=false`.

Railway, Render (paid tier) or any VPS with Docker work too: build the Dockerfile, mount a persistent volume at `/data`, set the same env vars, and keep one instance always on (the scheduler lives in the app).

## Command reference

```bash
python -m jobpilot doctor         # check your setup is ready (run this first)
python -m jobpilot serve          # dashboard + scheduler at http://localhost:8000
python -m jobpilot cycle          # one discovery/scoring pass in the terminal
python -m jobpilot detect Stripe  # check which ATS a company uses
pytest                            # tests, including a real browser fill against a mock form
```

## Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| Target companies, titles, locations, dealbreakers | profile (Settings page) | | Name alone usually works; add `careers_url` if a company shows "board not found" |
| `min_score` | profile | 70 | Raise it if your queue is noisy |
| Standard answers | profile `answers:` | | Matched against form labels before Claude is asked |
| `DRY_RUN` | env | true | |
| `MAX_APPLICATIONS_PER_DAY` | env | 15 | Hard cap on submissions |
| `MAX_SCORES_PER_RUN` | env | 60 | Caps Claude cost per run; target companies scored first |
| `DISCOVER_EVERY_HOURS` | env | 6 | |
| `CLAUDE_MODEL` | env | claude-sonnet-5-5 | |

## Project layout

```
jobpilot/
  sources/ats.py          Greenhouse, Lever, Ashby, SmartRecruiters, Workday + ATS auto-detection
  sources/aggregators.py  Adzuna, RemoteOK, LinkedIn (read-only, routes to company ATS)
  filters.py              free pre-filter
  scoring.py              Claude: fit assessment, cover letter, form answers (prompt-cached)
  apply/engine.py         Playwright form filler: prepare → review → submit
  apply/fields.js         in-page field extractor
  pipeline.py             discover → score → prepare → notify; approved → submit
  web.py, templates/      dashboard (HTTP basic auth) + scheduler
```

## When something breaks
ATS forms change. Every prepare and submit saves a screenshot, and failures go to **Needs you** with the reason, so problems are visible rather than silent. Most fixes are a new label pattern in `Filler.standard` (apply/engine.py) or an entry in your profile's `answers:`.

## Sharing this with friends
Each person should run **their own copy** with their own resume, profile, API key, and (if deployed) their own cloud instance. Don't share a running instance or point it at anyone else's accounts. When you pass the project along, send the zip or repo — never your `.env` or `data/` folder, which hold your key and personal data.

## License & credits
MIT License — see [LICENSE](LICENSE). Use it, change it, share it.

The fit-scoring rubric (eligibility/language gates, five-dimension scoring), the drafter–reviewer cover-letter pattern, and the ATS text-layer check are adapted from the MIT-licensed [ai-job-search](https://github.com/MadsLorentzen/ai-job-search) framework by Mads Lorentzen. Built to run with [Claude](https://www.anthropic.com) by Anthropic.

Not affiliated with, endorsed by, or sponsored by Anthropic, LinkedIn, Indeed, Greenhouse, Lever, Ashby, or any employer.
