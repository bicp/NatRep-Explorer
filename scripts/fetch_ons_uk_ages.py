"""Extract adults by single year of age and sex for the UK and its four nations from the ONS mid-year estimates.

Usage:  python scripts/fetch_ons_uk_ages.py [mye24tablesuk.xlsx]

Without a file argument the ONS workbook (~1 MB) is downloaded to a temporary folder. Writes data/ons_uk_age_sex.csv,
which scripts/build_db.py reads for the age splits of the United Kingdom and of England, Scotland, Wales and Northern
Ireland (the UN World Population Prospects data has no figures for the nations). Re-run when ONS publishes the next
mid-year estimates (usually in the summer): update URL below, then rebuild the database.

Source: Office for National Statistics, "Population estimates for the UK, England, Wales, Scotland and Northern
Ireland: mid-2024", table MYE2 (single year of age and sex), released 26 September 2025. Ages run to 90 (= 90 and over).
"""
import csv, os, sys, tempfile, urllib.request

import openpyxl

URL = ("https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/populationandmigration/populationestimates/"
       "datasets/populationestimatesforukenglandandwalesscotlandandnorthernireland/mid2024/mye24tablesuk.xlsx")
SRC = sys.argv[1] if len(sys.argv) > 1 else None
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "ons_uk_age_sex.csv")
MIN_AGE = 18
# ONS area code -> name used in the app (the nations are listed as "United Kingdom – <nation>")
AREAS = {"K02000001": ("United Kingdom", "GB"), "E92000001": ("United Kingdom – England", "GB-ENG"),
         "S92000003": ("United Kingdom – Scotland", "GB-SCT"), "W92000004": ("United Kingdom – Wales", "GB-WLS"),
         "N92000002": ("United Kingdom – Northern Ireland", "GB-NIR")}


def read_sheet(wb, title):
    """{area code: {age: persons}} from one MYE2 sheet (Males or Females)."""
    out, hdr = {}, None
    for r in wb[title].iter_rows(values_only=True):
        if r and r[0] == "Code": hdr = list(r); continue
        if hdr and r and r[0] in AREAS:
            ages = {}
            for h, v in zip(hdr, r):
                a = str(h).replace("+", "").strip() if h is not None else ""
                if a.isdigit(): ages[int(a)] = v
            if sum(ages.values()) != r[3]: sys.exit(f"{title}: ages of {r[0]} don't add up to 'All ages'")
            out[r[0]] = ages
    return out


def main():
    src = SRC
    if not src:
        src = os.path.join(tempfile.mkdtemp(), "mye24tablesuk.xlsx")
        print("downloading", URL)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r, open(src, "wb") as fh: fh.write(r.read())
    wb = openpyxl.load_workbook(src, read_only=True)
    males, females = read_sheet(wb, "MYE2 - Males"), read_sheet(wb, "MYE2 - Females")
    missing = sorted(set(AREAS) - set(males) - set(females))
    if missing: sys.exit(f"not in the ONS file: {', '.join(missing)}")
    rows = []
    for code, (name, iso) in AREAS.items():
        for age in sorted(males[code]):
            if age >= MIN_AGE:  # same layout as un_wpp_age_sex.csv: thousands of people
                rows.append((name, iso, age, males[code][age] / 1000, females[code][age] / 1000))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["country", "iso2", "age", "male_thousands", "female_thousands"])
        w.writerows(rows)
    print(f"{OUT}: {len(AREAS)} areas, {len(rows)} rows")


if __name__ == "__main__":
    main()
