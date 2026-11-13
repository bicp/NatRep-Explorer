"""Extract adult population by single year of age and sex for the app's countries from UN World Population Prospects.

Usage:  python scripts/fetch_un_ages.py [WPP2024_PopulationBySingleAgeSex_Medium_2024-2100.csv.gz] [year]

Without a file argument the UN file (~67 MB) is downloaded to a temporary folder. Writes data/un_wpp_age_sex.csv, which
scripts/build_db.py reads to build the age splits (18-24 ... 85+ in any bracket set). Re-run when the UN publishes a new
revision (every two years) or to move to a later reference year; then rebuild the database.

Source: United Nations, Department of Economic and Social Affairs, Population Division (2024).
World Population Prospects 2024, medium variant. Population on 1 July, in thousands. https://population.un.org/wpp/
"""
import csv, gzip, os, sys, tempfile, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_db import ISO2  # noqa: E402  (the app's 65 countries and their ISO codes)

URL = ("https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/"
       "WPP2024_PopulationBySingleAgeSex_Medium_2024-2100.csv.gz")
SRC = sys.argv[1] if len(sys.argv) > 1 else None
YEAR = sys.argv[2] if len(sys.argv) > 2 else "2025"
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "un_wpp_age_sex.csv")
MIN_AGE = 18


def main():
    src = SRC
    if not src:
        src = os.path.join(tempfile.mkdtemp(), os.path.basename(URL))
        print("downloading", URL)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as r, open(src, "wb") as fh:
            while b := r.read(1 << 20): fh.write(b)
    want = {iso: name for name, iso in ISO2.items()}
    out, found = [], set()
    with gzip.open(src, "rt", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            iso = r["ISO2_code"]
            if iso not in want or r["Time"] != YEAR or r["LocTypeName"] != "Country/Area": continue
            age = int(r["AgeGrpStart"])  # 100 = "100+"
            if age < MIN_AGE: continue
            out.append((want[iso], iso, age, float(r["PopMale"]), float(r["PopFemale"])))
            found.add(iso)
    missing = sorted(set(want) - found)
    if missing: sys.exit(f"not in the UN file: {', '.join(want[m] for m in missing)}")
    out.sort(key=lambda x: (x[0], x[2]))
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["country", "iso2", "age", "male_thousands", "female_thousands"])
        w.writerows(out)
    print(f"{OUT}: {len(found)} countries, {len(out)} rows, year {YEAR}")


if __name__ == "__main__":
    main()
