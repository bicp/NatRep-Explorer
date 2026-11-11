# NatRep Explorer

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

Requires Python 3.10+. Everything runs in the browser: there is no backend. The workbook is converted into a SQLite
database that the browser reads in small pieces over HTTP (with [sql.js-httpvfs](https://github.com/phiresky/sql.js-httpvfs)),
so only the parts a search needs are downloaded.

1. Copy the workbook into `data/` (keep the name `Global_Nat_Rep_and_Zip_Codes_Combined.xlsx`).
2. Mac/Linux: `./run.sh`  ·  Windows: double-click `run.bat`
3. Open http://localhost:8000

The first start converts the workbook into `data/natrep.db` (SQLite, ~1–2 minutes) and publishes it as chunks in
`app/static/db/` (plus `config.json`). After that it starts instantly. `scripts/serve.py` is a small static server
that supports HTTP Range requests, which the browser needs to read the database (Python's built-in server doesn't).

## Updating the data

When the workbook changes, replace the file in `data/` and run `./run.sh` (or `run.bat`): it rebuilds automatically
when the workbook is newer than the published database. Then redeploy (see *Redeploy* below).

To rebuild without starting the server:

```bash
.venv/bin/python scripts/build_db.py data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx data/natrep.db app/static/db
```

`.venv/` is created by `run.sh` on the first build and holds the importer's only dependency, `openpyxl`.
The importer reads the **Home** tab to find each country's Nat Rep and ZIP tabs, so new countries added there
are picked up automatically as long as they follow the existing layout.

## Deploy to Cloudflare Pages

`app/static/` is the whole site once the database is built. Deploy it as a **direct upload** with Wrangler (Cloudflare's
CLI), not through Git: the database chunks are built locally from the workbook and are not in the repository.
Requires Node.js (for `npx`); the free Cloudflare plan is enough.

### First deploy

1. Build the database if you haven't yet: run `./run.sh`, then stop it with Ctrl+C.
2. Log in to Cloudflare (opens the browser once):
   ```bash
   npx wrangler login
   ```
3. Create the Pages project (once):
   ```bash
   npx wrangler pages project create natrep-explorer --production-branch main
   ```
4. Upload the site:
   ```bash
   npx wrangler pages deploy app/static --project-name natrep-explorer --branch main
   ```
   The site is then live at `https://natrep-explorer.pages.dev`.
5. Check that Cloudflare serves byte ranges – the app can't read the database without them. Expect `HTTP/2 206`:
   ```bash
   curl -s -D - -o /dev/null -H "Range: bytes=0-1023" https://natrep-explorer.pages.dev/db/natrep.db.000 | head -1
   ```
6. Add a login. There is none built in, and anyone who can open the site can download the whole database.
   In the Cloudflare dashboard: **Zero Trust → Access → Applications → Add an application → Self-hosted**, add
   `natrep-explorer.pages.dev` and `*.natrep-explorer.pages.dev` (every deploy also gets its own preview URL), and a
   policy such as "emails ending in @yourcompany.com". Free for up to 50 users.

### Redeploy

After a code change or a new workbook:

1. If the workbook changed, replace it in `data/` and rebuild (see *Updating the data*).
2. Upload again – the same command as the first deploy:
   ```bash
   npx wrangler pages deploy app/static --project-name natrep-explorer --branch main
   ```

Each deploy replaces the whole site; earlier deploys stay listed under **Workers & Pages → natrep-explorer →
Deployments**, where you can roll back. Tabs that were open before a data update must be reloaded: until then they
mix old and new database files and show "database disk image is malformed".

### Limits

Pages allows files up to 25 MiB and 20,000 files per deploy. The database is published in 10 MB chunks
(`CHUNK_BYTES` in `scripts/build_db.py`), so it stays within both even if the workbook grows a lot.

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
app/static/              The whole app: index.html, styles.css, app.js, vendored Leaflet, Chart.js and sql.js-httpvfs
app/static/db/           Built database chunks + config.json (generated, not in git)
scripts/build_db.py      Workbook → SQLite importer; adds the indexes and lookup tables the browser queries, then splits
                         the file into chunks
scripts/serve.py         Local static server with HTTP Range support
```

The browser reads the database a few KB at a time, so every query in `app.js` must use an index or a precomputed
table (`places`, `regions`, `meta`). Check new queries with `EXPLAIN QUERY PLAN` – a `SCAN zips` would download most
of the file.
