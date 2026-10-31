"""Command line:

  python -m jobpilot doctor             # check your setup is ready (run this first)
  python -m jobpilot serve              # dashboard + scheduler (what the cloud deployment runs)
  python -m jobpilot cycle              # one discover -> score -> prepare pass, then exit
  python -m jobpilot discover           # just discovery
  python -m jobpilot detect "Stripe"    # which ATS does a company use?
  python -m jobpilot prepare <uid>      # pre-fill one application (no submit)
"""
from __future__ import annotations

import os
import sys


def doctor() -> int:
    """Friendly first-run check. Returns a non-zero exit code if something's missing."""
    from pathlib import Path

    from .config import load_settings

    ok = True

    def check(label: str, passed: bool, hint: str) -> None:
        nonlocal ok
        print(f"  [{'OK' if passed else 'XX'}] {label}")
        if not passed:
            ok = False
            print(f"         -> {hint}")

    print("JobPilot setup check\n")
    settings = load_settings()

    check("ANTHROPIC_API_KEY is set", bool(settings.anthropic_api_key),
          "Get a key at console.anthropic.com and put it in your .env file.")
    check("DASHBOARD_PASSWORD is set", bool(settings.dashboard_password),
          "Set DASHBOARD_PASSWORD in .env so your dashboard isn't open to anyone.")
    check(f"Data directory writable ({settings.data_dir})", os.access(settings.data_dir, os.W_OK),
          "Point DATA_DIR at a folder you can write to.")

    prof = settings.profile_path
    check(f"Profile exists ({prof})", prof.exists(),
          "Copy profile.example.yaml to there, or paste it on the dashboard's Settings page.")
    if prof.exists():
        try:
            from .config import load_profile
            p = load_profile(prof)
            check("Profile parses", True, "")
            check("Resume file exists",
                  bool(p.resume_path) and Path(p.resume_path).exists(),
                  "Upload your resume on the Settings page, or set candidate.resume_path in the profile.")
            check("At least one target title or company",
                  bool(p.titles or p.companies),
                  "Add some titles under preferences.titles or companies under companies.")
        except Exception as e:  # noqa: BLE001
            check("Profile parses", False, f"YAML error: {e}")

    try:
        import playwright  # noqa: F401
        check("Playwright installed", True, "")
    except ImportError:
        check("Playwright installed", False, "Run: pip install -r requirements.txt && playwright install chromium")

    print()
    if ok:
        print("All good. Start it with:  python -m jobpilot serve")
        print(f"DRY_RUN is {'ON (nothing will be submitted)' if settings.dry_run else 'OFF (applications WILL be submitted after you approve)'}.")
    else:
        print("Fix the items marked [XX] above, then run `python -m jobpilot doctor` again.")
    return 0 if ok else 1


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "serve"

    if cmd in {"-h", "--help", "help"}:
        print(__doc__)
        return
    if cmd == "doctor":
        sys.exit(doctor())
    if cmd == "serve":
        import uvicorn
        uvicorn.run("jobpilot.web:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
        return

    import logging
    logging.basicConfig(level=logging.INFO)
    from .config import load_settings
    from .db import DB
    from .pipeline import Pipeline

    settings = load_settings()
    p = Pipeline(settings, DB(settings.db_path))
    if cmd == "cycle":
        p.run_cycle()
    elif cmd == "discover":
        print(p.discover(), "new relevant jobs")
    elif cmd == "score":
        print(len(p.score()), "jobs above threshold")
    elif cmd == "detect":
        from .sources import detect_board
        print(detect_board(" ".join(sys.argv[2:])))
    elif cmd == "prepare":
        p.prepare(sys.argv[2])
    elif cmd == "apply":
        print(p.apply_approved(), "submitted")
    else:
        print(f"Unknown command: {cmd}\n")
        print(__doc__)


if __name__ == "__main__":
    main()
