#!/usr/bin/env bash
# One-command local setup for JobPilot. macOS / Linux.
# Windows: see the "Run locally on Windows" section in README.md.
set -e

echo "==> JobPilot setup"

# 1. Python deps
python3 -m pip install --upgrade pip >/dev/null
echo "==> Installing Python dependencies..."
python3 -m pip install -r requirements.txt

# 2. Browser for form-filling
echo "==> Installing Chromium for Playwright (one-time, ~150MB)..."
python3 -m playwright install chromium

# 3. .env
if [ ! -f .env ]; then
  cp .env.example .env
  echo "==> Created .env from the template. Open it and fill in ANTHROPIC_API_KEY and DASHBOARD_PASSWORD."
else
  echo "==> .env already exists, leaving it as is."
fi

# 4. data dir + profile
mkdir -p data
if [ ! -f data/profile.yaml ]; then
  cp profile.example.yaml data/profile.yaml
  echo "==> Created data/profile.yaml from the example. Edit it (or use the Settings page) to add your details."
fi

echo ""
echo "==> Done. Next steps:"
echo "    1. Edit .env   (add your Anthropic API key + a dashboard password)"
echo "    2. Put your resume in the data/ folder, or upload it on the Settings page later"
echo "    3. Load the env and check setup:"
echo "         set -a; . ./.env; set +a"
echo "         python3 -m jobpilot doctor"
echo "    4. Start it:   python3 -m jobpilot serve     (dashboard at http://localhost:8000)"
echo ""
echo "    Safety: DRY_RUN is ON by default, so nothing is submitted until you set DRY_RUN=false in .env."
