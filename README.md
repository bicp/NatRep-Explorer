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

The **Gender × age** and **Age splits** cards on the country page and the Compare age chart each have an age menu
(Compare follows the Age splits card). It offers the workbook's own bands (the Gender × age default) and six preset
brackets: standard consumer (the Age splits default), decade-based,
younger skew, senior / healthcare and two B2B sets, plus **Custom bands**, where the viewer types the ages at which
each band starts (e.g. `25, 35, 50, 65`: 18 always opens the first band and the last is open-ended). They are computed in the browser from adults by single year of
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

### UK nations

The **GB** tab holds a section per nation below the UK one (England, Scotland, Wales, Northern Ireland), each with
the same layout: a title in column B followed by *Internet penetration* and *Target* rows. The importer turns each
section into its own entry, *United Kingdom – England* etc. (codes GB-ENG, GB-SCT, GB-WLS, GB-NIR); the nations
have no zip tab of their own. Their figures come from ONS mid-2024 population estimates (gender and age), Ofcom
Technology Tracker 2026 (internet), OECD 2024 (education; England is the population-weighted mean of its 9
regions) and the DWP Family Resources Survey (gross household income bands, see below).

For the UK and the nations the age splits use the ONS single-year ages in `data/ons_uk_age_sex.csv` instead of the
UN data. To refresh them after the next ONS release, update `URL` in the script, run it, then rebuild the database:

```bash
.venv/bin/python scripts/fetch_ons_uk_ages.py
```

The UK income tables (columns Q–Y of each section) are **gross household income bands** (before tax, share of
households) from the DWP Family Resources Survey, the way survey respondents usually answer the income question.
The £200 weekly bands are shown as annual amounts (×52). The last three years are pooled in 2024/25 prices, as DWP
recommends for the smaller nations, and the UK is the sum of the four nations. The weighted household counts are in
`data/frs_household_gross_income.csv`, copied from DWP Stat-Xplore (guest login; the exact query is in
`scripts/add_frs_income.py`).

### Household income: real, not equivalised

Income tables show **actual household income** (or, where a country publishes nothing per household, income per
household member), not equivalised income. The tables, with source, table ID and URL for each, are in
`data/household_income.json`:

- per household: UK and nations (DWP FRS), Germany, Austria, Switzerland, Netherlands, Belgium, France, Ireland,
  Finland, Sweden, Norway, Spain, Portugal, Greece, Malta (2015, latest), Turkey
- per family: Denmark, Iceland
- per household member: Czech Republic, Poland, Slovakia, Bulgaria, Latvia, Estonia (persons 20–64)
- income groups with their published average real income (no band distribution exists): Hungary (2020, the
  latest per-capita series); cut-offs are midpoints between neighbouring averages and are labelled approximate
- no local table (`"remove": true` in the JSON): Cyprus, Romania, Slovenia, Lithuania publish no real household
  income distribution, so the country page shows only the comparable EUR bands (HHI Comparable tab)
- Eurostat EU-SILC equivalised bands, labelled as such, with the real average or median household income in the
  definition (only averages are published): Croatia, Serbia, Luxembourg

Bands as published, or decile/quartile limits where a country publishes only those (equal shares). To change or add
a country, edit its entry in the JSON (for the UK: update the CSV and run `scripts/add_frs_income.py`) and run:

```bash
.venv/bin/python scripts/write_income_tables.py
```

It replaces each listed tab's income table (and helper cells that read from it) and patches only those sheets inside
the workbook. Saving with openpyxl would drop the cached formula results that the importer reads in other tabs.
The app shows every income table of a tab as its own card; Compare uses the first one.

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
`scripts/build_db.py`; ~15,700 files / 1.03 GB today, 931,505 of the 2.4M zip rows being Brazilian CEPs), so it fits
up to ~1.3 GB, about 280 MB more. Beyond that, a larger `PAGE_SIZE` (fewer, bigger files) or R2 (Cloudflare's file
storage, which serves byte ranges) would be needed.
GitHub rejects files over 100 MB; the workbook is ~66 MB today. If it grows past that, move it to Git LFS.

### Zip coordinates

Most zip tabs are GeoNames postal-code exports. An October 2026 clean-up of the workbook had guessed where lost
decimal points belonged, and got it wrong for thousands of rows (Vadsø at longitude 2.97 instead of 29.7), and the
Switzerland and Austria tabs had their coordinates scrambled between rows. In November 2026 the coordinates were
checked and corrected:

- **GeoNames-based tabs:** every row was compared with the current GeoNames postal codes; 34,575 rows that differed
  got the GeoNames coordinates back (all of Switzerland and Austria, decimal slips in France, Spain, UK, Portugal,
  Norway, Russia, Peru, Ecuador, Morocco, and small GeoNames refinements in Australia, Colombia, Iceland, Germany).
- **Russia:** GeoNames itself places ~5,000 Russian postcodes at a same-named place in another region (Sakhalin
  villages in Kaliningrad). These were moved to the matching place in their own region (GeoNames gazetteer), or,
  without a match, to the centre of their postcode group (marked approximate).
- **Turkey / Mexico:** Northern Cyprus postcodes placed on the mainland and four Mexican clusters with one wrong shared
  point (Pánuco, Tecomán, Nadadores, Nuevo Laredo) were moved likewise.
- **Brazil:** each CEP range has the coordinates of its municipality's seat (`scripts/add_brazil_coords.py`, from
  `data/br_municipios.csv`), so radius search works at municipality level. Every state now has a file in `data/br_ceps/`, so
  Brazil has one row per CEP instead, built from IBGE's 2022 census address file (CNEFE), see below. The ranges are
  only used for a state without a file.

### Brazil: one point per CEP (IBGE census addresses)

IBGE's CNEFE lists every address counted in the 2022 census with its CEP, street and coordinates. Each CEP's point is
the median of its addresses on its own street (the street CEP Aberto gives for it, or its most common street), which
drops addresses filed under a mistyped CEP; small towns' generic CEPs (…-000) use all their addresses. The app shows the
CEP's neighbourhood, municipality and street, and its number of addresses and households (Census 2022). CEPs with no
census address (PO boxes, big companies) are not included. All 27 states: 931,505 CEPs (IBGE counts 931,499 CEPs
with addresses); in every state 83–99% of street CEPs have their addresses within 250 m of the point, and the centre
CEPs of 13 capitals checked are within 1.5 km of the city centre. CEP Aberto street names were used for São Paulo and
Ceará; without them about 1% of CEPs sit on the wrong street (in Ceará 1.2% moved more than 1 km when they were
added), so downloading CEP Aberto for more states and re-running the script improves those.

To add a state: download its file (`<code>_<UF>.zip`) from
[IBGE](https://ftp.ibge.gov.br/Cadastro_Nacional_de_Enderecos_para_Fins_Estatisticos/Censo_Demografico_2022/Arquivos_CNEFE/CSV/UF/)
(1 GB for SP, 3.7 GB for all states), optionally its CEP Aberto files (free account, ODbL) into `data/cepaberto/` for
the street names, then run and rebuild the database:

```bash
.venv/bin/python scripts/build_br_ceps.py path/to/35_SP.zip      # -> data/br_ceps/SP.csv.gz
```
- **Morocco:** the GeoNames file has no codes for the big cities; the official Barid Al-Maghrib 2018 lists
  (neighbourhood and locality codes, via data.gov.ma) were added, placed with GeoNames and OpenStreetMap.
- **Argentina:** Buenos Aires city (4-digit codes 1000–1499) was missing; 295 of its codes were added (516 rows,
  one per code and barrio) from the city's own address service (USIG), placed at the centre of the addresses sampled
  in each code. The samples (4,069 addresses) are in `data/caba_samples.jsonl`: the whole city every ~350 m, the
  centre, Palermo and Recoleta every ~120 m, where a code can cover only a few blocks. To sample again (it skips points
  already sampled, can be interrupted with Ctrl+C at any time, and the service answers about one address a second),
  then update the workbook (new codes are added, existing ones get the new positions) and rebuild:

  ```bash
  .venv/bin/python scripts/caba_postcodes.py sample dense
  .venv/bin/python scripts/caba_postcodes.py build
  .venv/bin/python scripts/add_zip_rows.py
  ```

Every change is listed in `data/zip_coord_fixes.csv` (sheet, row, old and new coordinates, source) and every added row
in `data/zip_additions.csv`; each tab's note (cell B2) says what was changed. To check the coordinates again after a
workbook update, rebuild the database and run:

```bash
.venv/bin/python scripts/check_zip_coords.py      # summary + zip_coordinate_check.csv with the flagged rows
.venv/bin/python scripts/apply_zip_fixes.py       # applies data/zip_coord_fixes.csv (only rows still holding the old value)
.venv/bin/python scripts/add_zip_rows.py          # appends data/zip_additions.csv (skips rows already there)
```

Remaining flags are mostly real places in very large regions (Western Australia, Yakutia, Baja California) or remote
islands; a few hundred scattered GeoNames rows (Bulgaria, Argentina, Uruguay) carry a wrong region label rather than a
wrong point and were left as they are.

## Data notes

- Radius search needs coordinates. **India**'s zip tab has none, so there you can search and export by place/region,
  but not by radius. **Brazil** has one point per CEP (from census addresses), so a radius finds streets and
  neighbourhoods.
- Zip search ignores spaces and hyphens: `01310100` finds `01310-100`.
- **UK:** the zip tab has postcode districts (SS2). A full postcode typed in the search (`SS2 6BH`, `ss26bh`) goes to
  its own point from the ONS Postcode Directory (all ~1.8 million live postcodes, `data/uk_postcodes.csv.gz`, Open
  Government Licence; contains Royal Mail, OS and ONS data), and the radius then lists the districts around it. A postcode
  that isn't there (terminated or mistyped) falls back to its district, with a note. To refresh after the next quarterly
  release (search "ONS Postcode Directory" on the ONS Open Geography Portal), then rebuild the database:

  ```bash
  .venv/bin/python scripts/build_uk_postcodes.py path/to/ONSPD_<MMM>_<YYYY>.zip   # -> data/uk_postcodes.csv.gz
  ```
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
