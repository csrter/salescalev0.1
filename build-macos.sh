#!/bin/bash

# Build the Salescale desktop app (.dmg) for Apple Silicon (arm64).
#
# Prerequisites on the build machine:
#   - Python 3.10+  (backend deps require it)
#   - Node.js 20.19+ or 22.12+  (Vite 8 requires it)
#
# Produces: electron-app/dist/Salescale-<version>-arm64.dmg

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# --- Backend: bundle the FastAPI server into a single binary ---
echo "--- Packaging Backend ---"
cd "$ROOT/backend"

python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Build from run.py (a launcher that gives the app real package context and
# starts uvicorn) rather than app/main.py, which has no runnable entrypoint.
# The collect flags pull in the dynamically-imported routers and native deps.
pyinstaller --name main --onefile --clean \
  --collect-submodules app \
  --collect-all psycopg \
  --collect-all uvicorn \
  --collect-all alembic \
  --collect-all mako \
  --hidden-import email_validator \
  --add-data "alembic:alembic" \
  --add-data "alembic.ini:." \
  run.py

deactivate

# --- Frontend: build the React app into static assets ---
echo "--- Building Frontend ---"
cd "$ROOT/frontend"
npm install
npm run build   # outputs to frontend/dist, bundled into the app by electron-builder

# --- Electron: package the .dmg (arm64 / Apple Silicon) ---
echo "--- Packaging Desktop App ---"
cd "$ROOT/electron-app"
npm install
# The electron-builder config lives in build/, so it must be passed explicitly.
npx electron-builder --mac --arm64 --config build/electron-builder.yml

echo "--- Build Complete ---"
echo "You can find the .dmg installer in the electron-app/dist directory."
