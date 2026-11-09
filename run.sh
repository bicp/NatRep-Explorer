#!/usr/bin/env bash
# Start NatRep Explorer on http://localhost:8000 (builds the database on first run).
set -e
cd "$(dirname "$0")"
XLSX="${1:-data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx}"
python3 -m pip install -q -r requirements.txt
if [ ! -f data/natrep.db ] || [ "$XLSX" -nt data/natrep.db ]; then
  [ -f "$XLSX" ] || { echo "Put the workbook at $XLSX (or pass its path as the first argument)"; exit 1; }
  echo "Building database from $XLSX (about 1–2 minutes)…"
  python3 scripts/build_db.py "$XLSX" data/natrep.db
fi
python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
