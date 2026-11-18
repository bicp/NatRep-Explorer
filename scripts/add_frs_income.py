"""Turn the DWP Family Resources Survey counts into the UK household income tables in data/household_income.json.

Usage:  python scripts/add_frs_income.py      then:  python scripts/write_income_tables.py

Reads data/frs_household_gross_income.csv (weighted household counts by financial year, UK nation and weekly income
band) and replaces the GB entries (United Kingdom, England, Scotland, Wales, Northern Ireland) in
data/household_income.json. scripts/write_income_tables.py then puts them on the GB tab.

The CSV comes from DWP Stat-Xplore (stat-xplore.dwp.gov.uk, guest login is enough):
  Family Resources Survey > Household Dataset
  Rows:    Household total, gross Income from all sources (in bands with 200 widths) in latest prices
           (weekly, CPI adjusted real terms) - all 11 bands
  Columns: Financial Year (the last three years) x Country of the Household in the United Kingdom (all four)
  Measure: Weighted count of Household Dataset (the default)
Copy the counts into the CSV (one row per year, nation and band). The three years are pooled, as DWP recommends
for the smaller nations, and the UK is the sum of the four nations, so the nations roll up to it exactly.
"""
import collections, csv, json, os

HERE = os.path.dirname(__file__)
CSV = os.path.join(HERE, "..", "data", "frs_household_gross_income.csv")
JSON = os.path.join(HERE, "..", "data", "household_income.json")
WEEKLY_EDGES = [200, 400, 600, 800, 1000, 1200, 1400, 1600, 1800, 2000]  # the Stat-Xplore £200 bands, per week
SECTIONS = {"United Kingdom": None, "England": "England", "Scotland": "Scotland", "Wales": "Wales", "Northern Ireland": "Northern Ireland"}


def annual_bands():
    """Weekly bands shown as annual amounts (x52)."""
    a = [e * 52 for e in WEEKLY_EDGES]
    return [f"Under £{a[0]:,}"] + [f"£{lo:,}–£{hi - 1:,}" for lo, hi in zip(a, a[1:])] + [f"£{a[-1]:,}+"]


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    years = sorted({r["financial_year"] for r in rows})
    weekly = list(dict.fromkeys(r["weekly_band"] for r in rows))
    assert len(weekly) == len(WEEKLY_EDGES) + 1, weekly
    cnt = collections.defaultdict(float)
    for r in rows:
        for who in (r["nation"], "United Kingdom"):
            cnt[(who, r["weekly_band"])] += float(r["households"])
    period = f"FYE {int(years[0][:4]) + 1}–FYE {int(years[-1][:4]) + 1} ({len(years)}-yr avg, {years[-1]} prices)"
    source = (f"DWP Family Resources Survey {years[0]} to {years[-1]} via Stat-Xplore (Household dataset): household total "
              "gross income from all sources, £200 weekly bands in latest prices (CPI-adjusted), weighted household counts "
              f"by country, {len(years)} years pooled; UK = sum of the four nations")
    new = []
    for who, section in SECTIONS.items():
        tot = sum(cnt[(who, b)] for b in weekly)
        new.append({"sheet": "GB", "section": section, "country": who, "year": period, "currency": "GBP",
                    "definition": "Gross household income from all sources, before tax (weekly £200 bands shown as annual ×52); share of households",
                    "source": source, "base": f"{tot / len(years) / 1e6:.2f}m households a year", "confidence": "High",
                    "bands": [{"label": lab, "share": round(cnt[(who, b)] / tot, 4)} for b, lab in zip(weekly, annual_bands())]})
    entries = json.load(open(JSON, encoding="utf-8")) if os.path.exists(JSON) else []
    entries = [e for e in entries if e["sheet"] != "GB"] + new
    with open(JSON, "w", encoding="utf-8") as fh: json.dump(entries, fh, ensure_ascii=False, indent=1)
    print(f"{JSON}: GB entries replaced ({len(new)}), {len(entries)} in total")


if __name__ == "__main__":
    main()
