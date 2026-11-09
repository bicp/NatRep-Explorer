@echo off
REM Start NatRep Explorer on http://localhost:8000 (builds the database on first run).
cd /d "%~dp0"
set XLSX=%1
if "%XLSX%"=="" set XLSX=data\Global_Nat_Rep_and_Zip_Codes_Combined.xlsx
python -m pip install -q -r requirements.txt
if not exist data\natrep.db (
  if not exist "%XLSX%" ( echo Put the workbook at %XLSX% & exit /b 1 )
  echo Building database from %XLSX% ^(about 1-2 minutes^)...
  python scripts\build_db.py "%XLSX%" data\natrep.db
)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
