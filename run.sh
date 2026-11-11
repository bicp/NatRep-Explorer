#!/usr/bin/env bash
# Start NatRep Explorer on http://localhost:8000 (builds the database on first run).
set -e
cd "$(dirname "$0")"
XLSX="${1:-data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx}"
if [ ! -f app/static/db/config.json ] || [ "$XLSX" -nt app/static/db/config.json ]; then
  [ -f "$XLSX" ] || { echo "Put the workbook at $XLSX (or pass its path as the first argument)"; exit 1; }
  # importer dependencies go in a project-local virtual environment (.venv/), not the global Python
  [ -x .venv/bin/python ] || python3 -m venv .venv
  .venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
  echo "Building database from $XLSX (about 1–2 minutes)…"
  .venv/bin/python scripts/build_db.py "$XLSX" data/natrep.db app/static/db
fi
python3 scripts/serve.py "${PORT:-8000}"
