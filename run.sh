#!/usr/bin/env bash
# ORION Validator - Phase 1
# One-command start: installs dependencies and runs the backend
# (which also serves the frontend at http://localhost:8000/).

set -e
cd "$(dirname "$0")/backend"

echo "Installing dependencies..."
pip install -r requirements.txt --quiet

echo ""
echo "Starting ORION Validator on http://localhost:8000"
echo "Login: aster-test / 123456"
echo ""
python3 main.py
