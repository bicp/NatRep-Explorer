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

1. The workbook is in the repo at `data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx` (keep that name when replacing it).
2. Mac/Linux: `./run.sh`  ·  Windows: double-click `run.bat`
3. Open http://localhost:8000

The first start converts the workbook into `data/natrep.db` (SQLite, ~1 minute) and publishes it in `app/static/db/`
as one file per 64 KB database page (plus `config.json`). After that it starts instantly. `scripts/serve.py` is a
small static server.

## Updating the data

1. Replace `data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx` with the new workbook (same name).
2. Check it locally: `./run.sh` (or `run.bat`) rebuilds automatically when the workbook is newer than the published
   database.
3. Commit the workbook and merge to `main` – the pipeline rebuilds the database and deploys it (see below).

To rebuild without starting the server:

```bash
.venv/bin/python scripts/build_db.py data/Global_Nat_Rep_and_Zip_Codes_Combined.xlsx data/natrep.db app/static/db
```

`.venv/` is created by `run.sh` on the first build and holds the importer's only dependency, `openpyxl`.
The importer reads the **Home** tab to find each country's Nat Rep and ZIP tabs, so new countries added there
are picked up automatically as long as they follow the existing layout.

### Age splits (UN population data)

The **Age splits** card (country page and Compare) offers six preset brackets: standard consumer, decade-based,
younger skew, senior / healthcare and two B2B sets. They are computed in the browser from adults by single year of
age and sex in `data/un_wpp_age_sex.csv`. That file is extracted from the UN
[World Population Prospects 2024](https://population.un.org/wpp/) (medium variant, population on 1 July 2025),
which is based on each country's censuses and surveys and covers all 65 countries in one consistent series.

To refresh it (new UN revision every two years, or a later reference year), then rebuild the database:

```bash
.venv/bin/python scripts/fetch_un_ages.py            # downloads the UN file (~67 MB), keeps 2025
.venv/bin/python scripts/fetch_un_ages.py "" 2026    # same, for another year
```

New countries need an entry in `ISO2` in `scripts/build_db.py`. The script stops if a country is missing from
the UN data. The brackets themselves are defined in `AGE_SPLITS` at the top of `app/static/app.js`.

## Deploy to Cloudflare Pages

`app/static/` is the whole site once the database is built – plain static files, no server code. The GitHub Actions
workflow
[`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) builds the database from the workbook in `data/` and
uploads the site with Wrangler (Cloudflare's CLI). The free Cloudflare plan is enough.

- **Push or merge to `main`** → production deploy at `https://natrep-explorer.pages.dev`.
- **Open or update a pull request** → preview deploy at `https://<branch>.natrep-explorer.pages.dev` (Cloudflare
  lowercases the branch name and replaces `/` and other symbols with `-`, e.g. `perf/faster-search` →
  `perf-faster-search`). The workflow comments the link on the pull request and updates that comment on every push.
  The preview is built from the pull request merged into `main`, so it shows what production will look like after
  merging. Pull requests from forks are skipped, since they don't get the Cloudflare secrets.
- **Actions → Deploy to Cloudflare Pages → Run workflow** on any branch → preview deploy of that branch.

Preview deploys never touch production. They use the same Cloudflare project, so the Access policy on
`*.natrep-explorer.pages.dev` (step 5 below) protects them too.

### One-time setup

1. **Create the Pages project.** In the Cloudflare dashboard: **Workers & Pages → Create → Pages → Upload assets**,
   name it `natrep-explorer`, and skip the upload. Or from a terminal with Node.js:
   ```bash
   npx wrangler login
   npx wrangler pages project create natrep-explorer --production-branch main
   ```
2. **Create an API token.** Dashboard → **My Profile → API Tokens → Create Token → Create Custom Token**:
   - Permissions: **Account · Cloudflare Pages · Edit**
   - Account resources: **Include · *your account***
   - Copy the token – it is shown once.
3. **Find your account ID.** Dashboard → **Workers & Pages**: *Account ID* in the right-hand column (also in the URL
   after `dash.cloudflare.com/`).
4. **Add both as GitHub secrets.** Repo → **Settings → Secrets and variables → Actions → New repository secret**:
   - `CLOUDFLARE_API_TOKEN` – the token from step 2
   - `CLOUDFLARE_ACCOUNT_ID` – the ID from step 3
5. **Add a login.** There is none built in, and anyone who can open the site can download the whole database.
   Dashboard → **Zero Trust → Access → Applications → Add an application → Self-hosted**, add
   `natrep-explorer.pages.dev` and `*.natrep-explorer.pages.dev` (preview deploys), and a policy such as
   "emails ending in @yourcompany.com". Free for up to 50 users.

### Deploy by hand

Without the pipeline – e.g. to try something quickly – build locally (`./run.sh`, then Ctrl+C) and upload. Run it
from the repo root so Wrangler also picks up `functions/`:

```bash
npx wrangler pages deploy app/static --project-name natrep-explorer --branch main
```

Use another `--branch` name for a preview deploy instead of production.

### Rolling back and open tabs

Each deploy replaces the whole site; earlier deploys stay listed under **Workers & Pages → natrep-explorer →
Deployments**, where you can roll back. Tabs that were open before a data update must be reloaded: until then they
mix old and new database files and show "database disk image is malformed".

### Limits

Pages allows 20,000 files per deploy. The database is published as one file per 64 KB page (`PAGE_SIZE` in
`scripts/build_db.py`; ~9,600 files today), so it fits up to ~1.3 GB – about twice today's size. If the workbook
grows past that, the database would need to move to R2 (Cloudflare's file storage, which serves byte ranges).
GitHub rejects files over 100 MB; the workbook is ~75 MB today. If it grows past that, move it to Git LFS.

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
app/static/db/           Built database, one file per 64 KB page, + config.json (generated, not in git)
scripts/build_db.py      Workbook → SQLite importer; adds the indexes and lookup tables the browser queries, then writes
                         one file per page
scripts/serve.py         Local static server
data/                    The source workbook (in git) and the built natrep.db (not in git)
.github/workflows/       deploy.yml – builds the database and deploys to Cloudflare Pages
```

The browser reads the database one 64 KB page (one request) at a time, so every query in `app.js` must use an index
or a precomputed table (`places`, `regions`, `meta`). Check new queries with `EXPLAIN QUERY PLAN` – a `SCAN zips`
would download most of the file. The vendored sql.js-httpvfs has two small patches, described in
`app/static/vendor/sql-httpvfs/PATCHES.md`.
