#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
set -e

# --- Backend Packaging ---
echo "--- Packaging Backend ---"
# Navigate to the backend directory
cd backend

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Package the application using PyInstaller
pyinstaller --name main --onefile --clean app/main.py

# Deactivate the virtual environment
deactivate

# Return to the root directory
cd ..

# --- Frontend Packaging ---
echo "--- Packaging Frontend ---"
# Navigate to the electron-app directory
cd electron-app

# Install Node.js dependencies
npm install

# Run electron-builder to create the .dmg
npm run dist

echo "--- Build Complete ---"
echo "You can find the .dmg installer in the electron-app/dist directory."
