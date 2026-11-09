# Pure NatRep Explorer

A small web app that makes **Global_Nat_Rep_and_Zip_Codes_Combined.xlsx** browsable for the team:

- **Countries** – Nat Rep figures per country: internet penetration, gender × age, education (national + ISCED),
  local household income bands, comparable EUR income bands, extra country tables (e.g. UK social grade, German
  Nielsen regions), sources and notes. Enter a **sample size** and every table shows the quota `n` per cell.
- **Compare** – up to six countries side by side (age, education, EUR income bands, internet), plus a full
  comparison table you can download as CSV.
- **Zip & radius search** – search any city, zip code or region across 58 countries (1.48M zip records). Pick a
  result and a radius (km) to get every zip code inside it, on a map and in a table; copy the list or download a CSV.
  Regions and places can also be exported as complete zip lists.

Light and dark mode: follows the computer's setting by default; the sun/moon button in the header switches and remembers the choice.

## Run it locally

Requires Python 3.10+.

1. Copy the workbook into `data/` (keep the name `Global_Nat_Rep_and_Zip_Codes_Combined.xlsx`).
2. Mac/Linux: `./run.sh`  ·  Windows: double-click `run.bat`
3. Open http://localhost:8000

The first start converts the workbook into `data/natrep.db` (SQLite, ~1–2 minutes). After that it starts instantly.

## Share it with the team

Run it on any always-on machine or small VM the team can reach, e.g. with Docker:

```bash
cp /path/to/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx data/
docker build -t natrep-explorer .
docker run -d -p 8000:8000 --name natrep natrep-explorer
```

There is no login built in – host it on the internal network/VPN, or put it behind your SSO proxy.

## Updating the data

When the workbook changes, replace the file in `data/` and rebuild:

```bash
python scripts/build_db.py data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx data/natrep.db
```

(`run.sh` rebuilds automatically when the workbook is newer than the database; with Docker, rebuild the image.)
The importer reads the **Home** tab to find each country's Nat Rep and ZIP tabs, so new countries added there
are picked up automatically as long as they follow the existing layout.

## Data notes

- Radius search needs coordinates. **India** and **Brazil** zip tabs have none (Brazil is CEP ranges), so for those
  you can search and export by place/region, but not by radius.
- No zip tabs exist for Cyprus, Finland, Greece, Hong Kong, Saudi Arabia, Taiwan and Vietnam.
- Saudi Arabia, Taiwan and Vietnam publish reference income figures only (no % by band).
- Distances are straight-line (haversine) from the selected point to each zip's centre point.
- GeoNames-based tabs can list several places per zip; results show one row per zip (nearest place),
  and the CSV lists all places in that zip.
- Map tiles come from OpenStreetMap; swap the tile URL in `app/static/app.js` if your network blocks it.

## Project layout

```
app/main.py          FastAPI backend (JSON API + serves the UI)
app/static/          UI: index.html, styles.css, app.js, vendored Leaflet + Chart.js
scripts/build_db.py  Workbook → SQLite importer (R*Tree index for radius, FTS5 for search)
```

API: `/api/countries`, `/api/countries/{name}`, `/api/search?q=`, `/api/radius?lat=&lon=&km=`,
`/api/radius.csv`, `/api/area?country=&level=admin1|admin2|place&value=`, `/api/area.csv`.
Interactive docs at `/docs`.
