@echo off
REM Start NatRep Explorer on http://localhost:8000 (builds the database on first run).
cd /d "%~dp0"
set XLSX=%1
if "%XLSX%"=="" set XLSX=data\Global_Nat_Rep_and_Zip_Codes_Combined.xlsx
if not exist app\static\db\config.json (
  if not exist "%XLSX%" ( echo Put the workbook at %XLSX% & exit /b 1 )
  REM importer dependencies go in a project-local virtual environment (.venv\), not the global Python
  if not exist .venv\Scripts\python.exe python -m venv .venv
  .venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt
  echo Building database from %XLSX% ^(about 1-2 minutes^)...
  .venv\Scripts\python scripts\build_db.py "%XLSX%" data\natrep.db app\static\db
)
python scripts\serve.py 8000
